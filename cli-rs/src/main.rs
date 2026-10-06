//! survey26 (Rust build): the official command-line tool of the GOSIM 2026 Agentic Observer Hackathon.
//!
//! A line-by-line port of cli/survey26.py. Commands, options and help come from the shared
//! cli/spec.json, error texts from cli/messages.json; tests/test_cli_conformance.py runs the
//! same scenarios against both builds and compares their --json output, exit codes and requests.

mod args;
mod util;

use args::{Args, Parsed};
use serde_json::{json, Map, Value};
use std::io::{IsTerminal, Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;
use util::*;

pub const SPEC: &str = include_str!("../../cli/spec.json");
const MESSAGES: &str = include_str!("../../cli/messages.json");
/// Presets of the website's "Add a model service" form, shared with it.
const MODEL_PROVIDERS: &str = include_str!("../../web/src/lib/modelProviders.json");
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
const DEFAULT_API: &str = "https://vdiemcofukuxglqsmlyz.supabase.co/functions/v1/survey26-cli";
const SITE: &str = "https://create.gosim.org/survey26/platform";
const ACTIVE: [&str; 2] = ["queued", "running"];
const FINISHED_RUN: [&str; 3] = ["scored", "failed", "cancelled"];
const REVISION_PENDING: [&str; 2] = ["queued", "preparing"];
const SELF_CHECK_RUNS: i64 = 3;

const EXIT_OK: i32 = 0;
const EXIT_ERROR: i32 = 1;
const EXIT_USAGE: i32 = 2;
const EXIT_AUTH: i32 = 3;
const EXIT_NOT_FOUND: i32 = 4;
const EXIT_RATE: i32 = 5;
const EXIT_UNAVAILABLE: i32 = 6;
const EXIT_TIMEOUT: i32 = 7;
const EXIT_LIMIT: i32 = 8;
const EXIT_FAILED: i32 = 9;

const AUTH_CODES: &[&str] = &["invalid_token", "cli_tokens_disabled", "account_banned", "account_unavailable", "login_required",
    "no_token", "banned", "not_authenticated"];
const LIMIT_CODES: &[&str] = &["daily_limit", "repeat_daily_limit", "preparation_limit", "preparation_daily_limit", "upload_limit",
    "batch_already_active", "team_variable_limit", "full", "team_limit_reached", "no_codes_left", "uid_daily_limit"];
const NOT_FOUND_CODES: &[&str] = &["revision_not_found", "run_not_found", "result_not_ready", "project_not_ready", "upload_not_found",
    "not_found", "token_not_found", "diagnostics_not_found", "invitation_not_found", "uid_not_found",
    "uid_unavailable", "request_not_found", "user_not_found", "repository_not_found",
    "source_ref_not_found", "source_subdir_not_found"];
const UNAVAILABLE_CODES: &[&str] = &["network_error", "gateway_unavailable", "portal_unavailable", "session_unavailable",
    "source_snapshot_unavailable", "artifact_service_unavailable", "request_failed"];

fn exit_code_for(code: &str) -> i32 {
    if AUTH_CODES.contains(&code) {
        EXIT_AUTH
    } else if code == "rate_limited" {
        EXIT_RATE
    } else if LIMIT_CODES.contains(&code) {
        EXIT_LIMIT
    } else if NOT_FOUND_CODES.contains(&code) {
        EXIT_NOT_FOUND
    } else if UNAVAILABLE_CODES.contains(&code) {
        EXIT_UNAVAILABLE
    } else if ["confirmation_required", "usage", "ambiguous_id", "file_not_found", "variables_exist"].contains(&code) {
        EXIT_USAGE
    } else if code == "wait_timeout" {
        EXIT_TIMEOUT
    } else {
        EXIT_ERROR
    }
}

#[derive(Debug)]
pub struct CliError {
    code: String,
    detail: String,
    exit_code: i32,
}

impl CliError {
    fn new(code: &str) -> Self {
        CliError { code: code.to_string(), detail: String::new(), exit_code: exit_code_for(code) }
    }
    fn detail(code: &str, detail: impl Into<String>) -> Self {
        CliError { code: code.to_string(), detail: detail.into(), exit_code: exit_code_for(code) }
    }
    fn exit(code: &str, detail: impl Into<String>, exit_code: i32) -> Self {
        CliError { code: code.to_string(), detail: detail.into(), exit_code }
    }
}

type R<T> = Result<T, CliError>;

fn messages() -> &'static Value {
    use std::sync::OnceLock;
    static M: OnceLock<Value> = OnceLock::new();
    M.get_or_init(|| serde_json::from_str(MESSAGES).expect("messages.json"))
}

fn language() -> String {
    let lang = ["SURVEY26_LANG", "LC_ALL", "LANG"].iter()
        .map(|k| std::env::var(k).unwrap_or_default()).find(|v| !v.is_empty()).unwrap_or_default();
    if lang.to_lowercase().starts_with("zh") { "zh".into() } else { "en".into() }
}

fn message_for(code: &str, lang: &str, detail: &str) -> String {
    match messages().get(code) {
        Some(pair) => s(&pair[if lang == "zh" { "zh" } else { "en" }]),
        None => if detail.is_empty() { code.to_string() } else { detail.to_string() },
    }
}

// ---------------------------------------------------------------------------------------------
// Configuration and transport

fn config_path() -> PathBuf {
    if let Ok(p) = std::env::var("SURVEY26_CONFIG") {
        if !p.is_empty() {
            return PathBuf::from(p);
        }
    }
    let base = std::env::var("XDG_CONFIG_HOME").ok().filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| {
        let home = std::env::var("HOME").or_else(|_| std::env::var("USERPROFILE")).unwrap_or_else(|_| ".".into());
        PathBuf::from(home).join(".config")
    });
    base.join("survey26").join("config.json")
}

fn read_config() -> Map<String, Value> {
    std::fs::read_to_string(config_path()).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| v.as_object().cloned()).unwrap_or_default()
}

fn write_config(data: &Map<String, Value>) -> R<PathBuf> {
    let path = config_path();
    let io = |e: std::io::Error| CliError::detail("config_error", e.to_string());
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(io)?;
    }
    let tmp = path.with_extension("tmp");
    {
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut f = options.open(&tmp).map_err(io)?;
        f.write_all(py_dumps(&Value::Object(data.clone())).as_bytes()).map_err(io)?;
    }
    std::fs::rename(&tmp, &path).map_err(io)?;
    Ok(path)
}

fn sleep_secs(seconds: f64) {
    let scale = std::env::var("SURVEY26_SLEEP_SCALE").ok().and_then(|v| v.parse::<f64>().ok()).unwrap_or(1.0);
    let total = seconds * scale;
    if total > 0.0 {
        std::thread::sleep(Duration::from_secs_f64(total));
    }
}

fn agent(timeout: u64) -> ureq::Agent {
    ureq::AgentBuilder::new().timeout_connect(Duration::from_secs(timeout)).timeout_read(Duration::from_secs(timeout))
        .timeout_write(Duration::from_secs(timeout)).try_proxy_from_env(true).build()
}

fn user_agent() -> String {
    format!("survey26-cli/{}", VERSION)
}

struct Api {
    token: Option<String>,
    url: String,
    retries: u32,
    agent: ureq::Agent,
}

impl Api {
    fn call(&self, op: &str, write: bool, fields: Value) -> R<Value> {
        let token = match &self.token {
            Some(t) if !t.is_empty() => t.clone(),
            _ => return Err(CliError::new("no_token")),
        };
        if !valid_token(&token) {
            return Err(CliError::new("invalid_token"));
        }
        let mut body = Map::new();
        body.insert("op".into(), Value::String(op.into()));
        if let Value::Object(f) = fields {
            for (k, v) in f {
                body.insert(k, v);
            }
        }
        let body = serde_json::to_string(&Value::Object(body)).unwrap();
        let mut attempt = 0;
        loop {
            attempt += 1;
            let response = self.agent.post(&self.url).set("Authorization", &format!("Bearer {}", token))
                .set("Content-Type", "application/json").set("User-Agent", &user_agent()).send_string(&body);
            match response {
                Ok(resp) => match resp.into_string() {
                    Ok(text) => {
                        let text = if text.is_empty() { "{}".to_string() } else { text };
                        let payload: Value = serde_json::from_str(&text).map_err(|_| CliError::new("invalid_api_response"))?;
                        return match payload.as_object().and_then(|o| o.get("data")) {
                            Some(d) => Ok(d.clone()),
                            None => Err(CliError::new("invalid_api_response")),
                        };
                    }
                    Err(e) => {
                        if attempt >= self.retries || write {
                            return Err(CliError::detail("network_error", e.to_string()));
                        }
                    }
                },
                Err(ureq::Error::Status(status, resp)) => {
                    let text = resp.into_string().unwrap_or_default();
                    let mut code = serde_json::from_str::<Value>(&text).ok()
                        .and_then(|v| v.get("error").cloned()).filter(truthy).map(|v| py_str(&v))
                        .unwrap_or_else(|| "request_failed".into());
                    if code.contains("violates foreign key constraint") && code.contains("on table \"teams\"") {
                        code = "has_submissions".into(); // a team with projects cannot be emptied or disbanded
                    }
                    let retry = [502, 503, 504].contains(&status) && !write;
                    if !retry || attempt >= self.retries {
                        return Err(CliError::new(&code));
                    }
                }
                Err(ureq::Error::Transport(t)) => {
                    // Refused or unresolvable: the request never left this computer, so a retry is safe.
                    let never_sent = matches!(t.kind(), ureq::ErrorKind::ConnectionFailed | ureq::ErrorKind::Dns);
                    if attempt >= self.retries || (write && !never_sent) {
                        return Err(CliError::detail("network_error", t.to_string()));
                    }
                }
            }
            sleep_secs(std::cmp::min(2u64.pow(attempt), 8) as f64);
        }
    }

    fn rpc(&self, name: &str, write: bool, args: Value) -> R<Value> {
        self.call("rpc", write, json!({"name": name, "args": args}))
    }

    fn portal(&self, action: &str, write: bool, fields: Value) -> R<Value> {
        let mut f = Map::new();
        f.insert("action".into(), Value::String(action.into()));
        if let Value::Object(o) = fields {
            for (k, v) in o {
                f.insert(k, v);
            }
        }
        self.call("portal", write, json!({"fields": Value::Object(f)}))
    }
}

fn valid_token(t: &str) -> bool {
    t.len() == 68 && t.starts_with("s26_") && t[4..].bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn http_get(url: &str) -> R<Vec<u8>> {
    let a = agent(120);
    for attempt in 1..=3u32 {
        let result = a.get(url).set("User-Agent", &user_agent()).call();
        let err = match result {
            Ok(resp) => {
                let mut buf = Vec::new();
                match resp.into_reader().read_to_end(&mut buf) {
                    Ok(_) => return Ok(buf),
                    Err(e) => e.to_string(),
                }
            }
            Err(e) => e.to_string(),
        };
        if attempt == 3 {
            return Err(CliError::detail("download_failed", err));
        }
        sleep_secs((attempt * 2) as f64);
    }
    Err(CliError::new("download_failed"))
}

fn http_put(url: &str, data: &[u8], headers: &[(&str, String)]) -> R<()> {
    let mut req = agent(600).put(url);
    for (k, v) in headers {
        req = req.set(k, v);
    }
    match req.send_bytes(data) {
        Ok(resp) => {
            let _ = resp.into_string();
            Ok(())
        }
        Err(ureq::Error::Status(code, resp)) => {
            let text = resp.into_string().unwrap_or_default();
            // Re-sending a part that already arrived is fine (the website treats it the same way).
            if (code == 400 || code == 409) && (text.contains("Duplicate") || text.contains("already exists")) {
                return Ok(());
            }
            Err(CliError::detail("upload_failed", text.chars().take(200).collect::<String>()))
        }
        Err(e) => Err(CliError::detail("upload_failed", e.to_string())),
    }
}

// ---------------------------------------------------------------------------------------------
// Output

pub struct Out {
    json: bool,
    lang: String,
}

impl Out {
    fn t<'a>(&self, en: &'a str, zh: &'a str) -> &'a str {
        if self.lang == "zh" { zh } else { en }
    }
    fn line(&self, text: &str) {
        if !self.json {
            println!("{}", text);
            let _ = std::io::stdout().flush();
        }
    }
    fn table(&self, rows: &[Value], columns: &[(&str, &str)]) {
        if self.json {
            return;
        }
        if rows.is_empty() {
            println!("{}", self.t("(none)", "（无）"));
            return;
        }
        let mut cells: Vec<Vec<String>> = vec![columns.iter().map(|c| c.0.to_string()).collect()];
        for r in rows {
            cells.push(columns.iter().map(|c| blank_none(r.get(c.1).unwrap_or(&Value::Null))).collect());
        }
        let widths: Vec<usize> = (0..columns.len()).map(|i| cells.iter().map(|row| row[i].chars().count()).max().unwrap_or(0)).collect();
        for (index, row) in cells.iter().enumerate() {
            let line: Vec<String> = row.iter().enumerate().map(|(i, c)| format!("{}{}", c, " ".repeat(widths[i] - c.chars().count()))).collect();
            println!("{}", line.join("  ").trim_end());
            if index == 0 {
                println!("{}", widths.iter().map(|w| "-".repeat(*w)).collect::<Vec<_>>().join("  "));
            }
        }
    }
}

fn interactive(a: &Args) -> bool {
    !a.json && std::io::stdin().is_terminal() && std::io::stdout().is_terminal()
}

fn read_line_stdin() -> String {
    let mut line = String::new();
    let _ = std::io::stdin().read_line(&mut line);
    line
}

fn read_all_stdin() -> String {
    let mut text = String::new();
    let _ = std::io::stdin().read_to_string(&mut text);
    text
}

/// Reads a line without echo (like getpass) where the terminal allows it.
fn read_hidden(prompt: &str) -> String {
    eprint!("{}", prompt);
    let _ = std::io::stderr().flush();
    #[cfg(unix)]
    let off = std::process::Command::new("stty").arg("-echo").stdin(std::process::Stdio::inherit()).status().is_ok();
    let line = read_line_stdin();
    #[cfg(unix)]
    if off {
        let _ = std::process::Command::new("stty").arg("echo").stdin(std::process::Stdio::inherit()).status();
        eprintln!();
    }
    line.trim_end_matches(['\r', '\n']).to_string()
}

fn confirm(a: &Args, out: &Out, en: &str, zh: &str) -> R<()> {
    if a.flag("yes") {
        return Ok(());
    }
    if interactive(a) {
        print!("{} [y/N] ", out.t(en, zh));
        let _ = std::io::stdout().flush();
        let answer = read_line_stdin().trim().to_lowercase();
        if ["y", "yes", "是"].contains(&answer.as_str()) {
            return Ok(());
        }
        return Err(CliError::exit("cancelled", out.t("Cancelled.", "已取消。"), EXIT_USAGE));
    }
    Err(CliError::detail("confirmation_required", out.t(en, zh)))
}

// ---------------------------------------------------------------------------------------------
// Helpers shared by commands

const GREEK: [&str; 4] = ["alpha", "beta", "gamma", "delta"];

fn practice_card(slug: &str) -> Option<usize> {
    slug.strip_prefix("v4-practice-").and_then(|g| GREEK.iter().position(|x| *x == g))
}

fn task_card(slug: &str) -> Option<char> {
    let rest = slug.strip_prefix("v4-")?;
    let mut chars = rest.chars();
    let c = chars.next()?;
    if chars.next().is_none() && ('a'..='h').contains(&c) { Some(c) } else { None }
}

fn scenario_label(slug: &str, name: &str, lang: &str) -> String {
    if let Some(i) = practice_card(slug) {
        let symbol = ["α", "β", "γ", "δ"][i];
        return format!("{}{}", if lang == "zh" { "练习卡 " } else { "Practice card " }, symbol);
    }
    if let Some(c) = task_card(slug) {
        return format!("{}{}", if lang == "zh" { "任务卡 " } else { "Card " }, c.to_ascii_uppercase());
    }
    if !name.is_empty() { name.to_string() } else { slug.to_string() }
}

fn scenario_order(slug: &str) -> f64 {
    if let Some(i) = practice_card(slug) {
        return i as f64;
    }
    if let Some(c) = task_card(slug) {
        return (c as u8 - b'a') as f64;
    }
    f64::INFINITY
}

fn card_folder_name(slug: &str, fallback: &str) -> String {
    let base = slug.strip_prefix("v4-").unwrap_or(slug);
    let mut folder = String::new();
    let mut in_bad = false;
    for ch in base.chars() {
        if ch.is_ascii_alphanumeric() || ch == '.' || ch == '_' || ch == '-' {
            folder.push(ch);
            in_bad = false;
        } else if !in_bad {
            folder.push('-');
            in_bad = true;
        }
    }
    let folder = folder.trim_matches('-').to_string();
    if folder.is_empty() { fallback.to_string() } else { folder }
}

fn ordered_card_folder(index: usize, total: usize, folder: &str) -> String {
    format!("{:0width$}-{}", index + 1, folder, width = total.to_string().len())
}

/// Same as the website: drop the single '<repository>-<commit>/' wrapper of a result kept on GitHub.
fn flatten_result_entries(entries: Vec<(String, Vec<u8>)>) -> Vec<(String, Vec<u8>)> {
    let entries: Vec<(String, Vec<u8>)> = entries.into_iter().filter(|(n, _)| !n.ends_with('/')).collect();
    let mut tops: Vec<String> = Vec::new();
    for (n, _) in &entries {
        if let Some(i) = n.find('/') {
            let t = n[..=i].to_string();
            if !tops.contains(&t) {
                tops.push(t);
            }
        }
    }
    let wrapped = tops.len() == 1 && entries.iter().all(|(n, _)| n.contains('/') || n == "agent.log");
    let top = if wrapped { tops[0].clone() } else { String::new() };
    let mut out: Vec<(String, Vec<u8>)> = Vec::new();
    for (name, content) in entries {
        let flat = if !top.is_empty() && name.starts_with(&top) { name[top.len()..].to_string() } else { name.clone() };
        match out.iter().position(|(n, _)| *n == flat) {
            None => out.push((flat, content)),
            Some(i) => {
                if name == flat {
                    out[i].1 = content;
                }
            }
        }
    }
    out
}

fn resolve_id(prefix: &str, ids: &[String], kind: &str) -> R<String> {
    let value = prefix.trim().to_lowercase();
    if is_uuid(&value) {
        return Ok(value);
    }
    if value.chars().count() < 4 {
        return Err(CliError::exit("usage", format!("{} ID: give the full ID or at least 4 characters", kind), EXIT_USAGE));
    }
    let mut matches: Vec<&String> = ids.iter().filter(|i| i.starts_with(&value)).collect();
    matches.sort();
    matches.dedup();
    if matches.len() > 1 {
        return Err(CliError::new("ambiguous_id"));
    }
    match matches.first() {
        Some(m) => Ok((*m).clone()),
        None => Err(CliError::exit("not_found", format!("no {} matches {}", kind, prefix), EXIT_NOT_FOUND)),
    }
}

fn portal_list(api: &Api) -> R<Value> {
    let v = api.portal("list", false, json!({}))?;
    Ok(if truthy(&v) { v } else { json!({}) })
}

/// portal_list for wait loops: a rate limit or a temporary outage pauses the wait instead of ending it.
fn polled_list(api: &Api, deadline: f64) -> R<Value> {
    loop {
        match portal_list(api) {
            Ok(v) => return Ok(v),
            Err(e) => {
                if (e.exit_code != EXIT_RATE && e.exit_code != EXIT_UNAVAILABLE) || now() >= deadline {
                    return Err(e);
                }
                sleep_secs(if e.exit_code == EXIT_RATE { 60.0 } else { 20.0 });
            }
        }
    }
}

fn all_revisions(data: &Value) -> Vec<Value> {
    let mut rows = Vec::new();
    for project in arr(&data["projects"]) {
        for r in arr(&project["observer_revisions"]) {
            let mut r = r.clone();
            if let Value::Object(o) = &mut r {
                o.insert("title".into(), project.get("title").cloned().unwrap_or(Value::Null));
            }
            rows.push(r);
        }
    }
    // Newest first; stable like Python's sort(reverse=True).
    rows.sort_by(|a, b| s_or(&b["created_at"]).cmp(&s_or(&a["created_at"])));
    rows
}

fn find_revision(data: &Value, prefix: &str) -> R<Value> {
    let revisions = all_revisions(data);
    let ids: Vec<String> = revisions.iter().map(|r| s(&r["id"])).collect();
    let rid = resolve_id(prefix, &ids, "version")?;
    revisions.into_iter().find(|r| s(&r["id"]) == rid).ok_or_else(|| CliError::new("revision_not_found"))
}

fn find_batch(data: &Value, prefix: &str) -> R<Value> {
    let batches = arr(&data["batches"]);
    if prefix == "latest" || prefix == "last" {
        return batches.first().cloned().ok_or_else(|| CliError::exit("not_found", "no evaluations yet", EXIT_NOT_FOUND));
    }
    let ids: Vec<String> = batches.iter().map(|b| s(&b["id"])).collect();
    let bid = resolve_id(prefix, &ids, "evaluation")?;
    batches.iter().find(|b| s(&b["id"]) == bid).cloned()
        .ok_or_else(|| CliError::exit("not_found", "no such evaluation of your team (only the latest 50 are listed)", EXIT_NOT_FOUND))
}

fn find_run(data: &Value, prefix: &str) -> R<(Option<Value>, Value)> {
    let mut runs = Vec::new();
    for b in arr(&data["batches"]) {
        for r in arr(&b["observer_runs"]) {
            runs.push((b.clone(), r.clone()));
        }
    }
    let lower = prefix.to_lowercase();
    if is_uuid(&lower) {
        for (b, r) in &runs {
            if s(&r["id"]) == lower {
                return Ok((Some(b.clone()), r.clone()));
            }
        }
        return Ok((None, json!({"id": lower, "scenario_id": null})));
    }
    let ids: Vec<String> = runs.iter().map(|(_, r)| s(&r["id"])).collect();
    let rid = resolve_id(prefix, &ids, "run")?;
    runs.into_iter().find(|(_, r)| s(&r["id"]) == rid).map(|(b, r)| (Some(b), r)).ok_or_else(|| CliError::new("run_not_found"))
}

fn scenario_names(api: &Api, data: &Value) -> R<Map<String, Value>> {
    let mut ids: Vec<String> = Vec::new();
    for b in arr(&data["batches"]) {
        for r in arr(&b["observer_runs"]) {
            if truthy(&r["scenario_id"]) {
                ids.push(s(&r["scenario_id"]));
            }
        }
    }
    ids.sort();
    ids.dedup();
    let mut names = Map::new();
    for chunk in ids.chunks(100) {
        for row in arr(&api.call("scenarios", false, json!({"ids": chunk}))?) {
            names.insert(s(&row["id"]), row.clone());
        }
    }
    Ok(names)
}

/// The phase the website's evaluate button uses (the entry phase first), or --phase.
fn competition_phase(api: &Api, data: &Value, wanted: Option<&str>) -> R<Value> {
    let t = now();
    let mut phases = Vec::new();
    for p in arr(&data["phases"]) {
        let info = &p["phases"];
        let ends = parse_time(&s_or(&info["ends_at"]));
        let starts = parse_time(&s_or(&info["starts_at"]));
        if truthy(&info["is_active"]) && ends.map_or(true, |e| e > t) && starts.map_or(true, |st| st <= t) {
            let mut q = p.clone();
            if let Value::Object(o) = &mut q {
                for k in ["slug", "name_en", "name_zh", "ends_at"] {
                    o.insert(k.into(), info.get(k).cloned().unwrap_or(Value::Null));
                }
            }
            phases.push(q);
        }
    }
    if let Some(w) = wanted.filter(|w| !w.is_empty()) {
        return phases.into_iter().find(|p| s_or(&p["slug"]) == w || s_or(&p["phase_id"]) == w)
            .ok_or_else(|| CliError::new("phase_closed"));
    }
    let comp = api.rpc("current_competition", false, json!({}))?;
    let mut beta = Value::Null;
    if s_or(&comp["mode"]) == "competition" {
        beta = api.rpc("my_observer_phase", false, json!({})).unwrap_or(Value::Null);
    }
    let allowed: Vec<Value> = [beta, comp["project_phase_id"].clone(), comp["phase_id"].clone()].into_iter().filter(truthy).collect();
    for preferred in &allowed {
        if let Some(p) = phases.iter().find(|p| &p["phase_id"] == preferred) {
            return Ok(p.clone());
        }
    }
    Err(CliError::new("no_open_phase"))
}

fn batch_summary(batch: &Value, names: &Map<String, Value>, lang: &str) -> Value {
    let mut runs: Vec<Value> = Vec::new();
    for run in arr(&batch["observer_runs"]) {
        let sc = names.get(&s_or(&run["scenario_id"])).cloned().unwrap_or(json!({}));
        runs.push(obj(vec![
            ("run_id", run["id"].clone()),
            ("scenario_id", run.get("scenario_id").cloned().unwrap_or(Value::Null)),
            ("card", sc.get("slug").cloned().unwrap_or(Value::Null)),
            ("label", Value::String(scenario_label(&s_or(&sc["slug"]), &s_or(&sc["name"]), lang))),
            ("status", run.get("status").cloned().unwrap_or(Value::Null)),
            ("score", run.get("score").cloned().unwrap_or(Value::Null)),
            ("has_result", Value::Bool(truthy(&run["result_path"]))),
        ]));
    }
    runs.sort_by(|a, b| scenario_order(&s_or(&a["card"])).partial_cmp(&scenario_order(&s_or(&b["card"]))).unwrap());
    obj(vec![
        ("batch_id", batch["id"].clone()),
        ("status", g(batch, "status")),
        ("score", g(batch, "score")),
        ("phase_id", g(batch, "phase_id")),
        ("revision_id", g(batch, "revision_id")),
        ("created_at", g(batch, "created_at")),
        ("quota_refunded", Value::Bool(truthy(&batch["quota_refunded"]))),
        ("repeat_group", g(batch, "repeat_group")),
        ("repeat_runs", g(batch, "repeat_runs")),
        ("model_disabled", Value::Bool(truthy(&batch["model_disabled"]))),
        ("runs", Value::Array(runs)),
    ])
}

fn fmt_score(v: &Value) -> String {
    match v.as_f64() {
        Some(f) => format!("{:.2}", f),
        None => "-".into(),
    }
}

fn time_hms() -> String {
    let secs = now() as i64;
    let (h, m, sec) = ((secs / 3600) % 24, (secs / 60) % 60, secs % 60);
    format!("{:02}:{:02}:{:02}Z", h, m, sec)
}

// ---------------------------------------------------------------------------------------------
// Commands: account and profile

fn public_me(me: &Value) -> Value {
    let keys = ["id", "uid", "email", "name", "nickname", "github", "affiliation", "role", "locale", "city", "contact", "blurb",
        "show_on_wall", "looking_for_team", "seeking", "seeking_count", "astro_level", "ai_level", "avatar_url"];
    let mut result = Map::new();
    for k in keys {
        result.insert(k.into(), g(me, k));
    }
    let team = g(me, "team");
    if !truthy(&team) {
        result.insert("team".into(), Value::Null);
    } else {
        let mut t = Map::new();
        for k in ["id", "name", "leader_id", "invite_code", "max_size", "member_count", "is_locked", "github_repo", "project_idea"] {
            t.insert(k.into(), g(&team, k));
        }
        t.insert("is_captain".into(), Value::Bool(g(&team, "leader_id") == g(me, "id")));
        result.insert("team".into(), Value::Object(t));
    }
    Value::Object(result)
}

fn whoami(api: &Api) -> R<Value> {
    let v = api.call("whoami", false, json!({}))?;
    let me = g(&or_empty(v), "me");
    Ok(or_empty(me))
}

fn cmd_login(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let mut token = a.str("token").unwrap_or_default();
    if a.flag("token_stdin") {
        token = read_line_stdin().trim().to_string();
    }
    if token.is_empty() {
        if interactive(a) {
            token = read_hidden(out.t("API token (s26_...): ", "API 令牌（s26_...）：")).trim().to_string();
        } else {
            return Err(CliError::exit("usage", "pass --token TOKEN or --token-stdin", EXIT_USAGE));
        }
    }
    api.token = Some(token.clone());
    let me = api.call("whoami", false, json!({}))?;
    let mut config = read_config();
    config.insert("token".into(), Value::String(token));
    let path = write_config(&config)?;
    let who = or_empty(g(&or_empty(me), "me"));
    let display = if truthy(&who["nickname"]) { py_str(&who["nickname"]) } else { py_none_str(&g(&who, "name")) };
    out.line(&format!("{}", out.t("Logged in as {0} ({1}). Token saved to {2}", "已登录：{0}（{1}）。令牌已保存到 {2}")
        .replace("{0}", &display).replace("{1}", &py_none_str(&g(&who, "email"))).replace("{2}", &path.display().to_string())));
    Ok(obj(vec![("user", public_me(&who)), ("config_path", Value::String(path.display().to_string()))]))
}

fn cmd_logout(_api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let mut config = read_config();
    let removed = config.remove("token").is_some();
    if removed {
        write_config(&config)?;
    }
    out.line(out.t("Token removed from this computer. It stays valid until you revoke it on your profile page.",
        "已从本机删除令牌。令牌本身仍有效，如需作废请在个人资料页撤销。"));
    Ok(obj(vec![("removed", Value::Bool(removed)), ("config_path", Value::String(config_path().display().to_string()))]))
}

fn cmd_whoami(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let me = public_me(&whoami(api)?);
    let display = if truthy(&me["nickname"]) { py_str(&me["nickname"]) } else { py_none_str(&me["name"]) };
    out.line(&format!("{}  {}", display, py_none_str(&me["email"])));
    if truthy(&me["uid"]) {
        out.line(&format!("UID {}", py_str(&me["uid"])));
    }
    let team = &me["team"];
    let tail = if truthy(team) {
        format!("{} ({})", py_none_str(&team["name"]), if truthy(&team["is_captain"]) { out.t("captain", "队长") } else { out.t("member", "队员") })
    } else {
        out.t("none", "无").to_string()
    };
    out.line(&format!("{}{}", out.t("Team: ", "队伍："), tail));
    Ok(me)
}

fn cmd_profile_show(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let me = public_me(&whoami(api)?);
    for (k, v) in me.as_object().unwrap() {
        if k != "team" {
            out.line(&format!("{:<16} {}", k, blank_none(v)));
        }
    }
    Ok(me)
}

const PROFILE_OPTIONS: [&str; 9] = ["name", "nickname", "github", "affiliation", "role", "locale", "city", "contact", "blurb"];

fn cmd_profile_set(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let mut fields = Map::new();
    for k in PROFILE_OPTIONS {
        if let Some(v) = a.str(k) {
            fields.insert(k.into(), Value::String(v));
        }
    }
    for k in ["astro_level", "ai_level"] {
        if let Some(v) = a.int(k) {
            fields.insert(k.into(), json!(v));
        }
    }
    if fields.is_empty() {
        return Err(CliError::exit("usage", "nothing to change (see survey26 profile set --help)", EXIT_USAGE));
    }
    if let Some(n) = fields.get("name") {
        if py_strip(&s(n)).is_empty() {
            return Err(CliError::new("name_required"));
        }
    }
    if let Some(n) = fields.get("nickname") {
        if py_strip(&s(n)).chars().count() > 40 {
            return Err(CliError::new("nickname_too_long"));
        }
    }
    if let Some(v) = fields.get("github").cloned() {
        fields.insert("github".into(), Value::String(py_strip(&s(&v)).trim_start_matches('@').to_string()));
    }
    if let Some(v) = fields.get("blurb").cloned() {
        fields.insert("blurb".into(), Value::String(py_strip(&s(&v)).chars().take(160).collect()));
    }
    for (_, v) in fields.iter_mut() {
        if let Value::String(t) = v {
            *t = py_strip(t);
        }
    }
    let res = api.call("profile_update", true, json!({"fields": Value::Object(fields)}))?;
    let me = public_me(&or_empty(g(&or_empty(res), "me")));
    out.line(out.t("Profile saved.", "资料已保存。"));
    Ok(me)
}

fn cmd_avatar_set(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let path = PathBuf::from(a.str("file").unwrap_or_default());
    if !path.is_file() {
        return Err(CliError::new("file_not_found"));
    }
    let data = std::fs::read(&path).map_err(|_| CliError::new("file_not_found"))?;
    let kind = if data.starts_with(b"\x89PNG\r\n\x1a\n") {
        "image/png"
    } else if data.starts_with(b"\xff\xd8\xff") {
        "image/jpeg"
    } else if data.len() >= 12 && &data[..4] == b"RIFF" && &data[8..12] == b"WEBP" {
        "image/webp"
    } else {
        ""
    };
    if kind.is_empty() {
        return Err(CliError::new("avatar_bad_type"));
    }
    if data.len() > 2 * 1024 * 1024 {
        return Err(CliError::new("avatar_too_large"));
    }
    let result = api.call("avatar_upload", true, json!({"content_type": kind, "data": base64(&data)}))?;
    out.line(&format!("{}{}", out.t("Avatar updated: ", "头像已更新："), py_none_str(&g(&or_empty(result.clone()), "avatar_url"))));
    Ok(result)
}

fn cmd_avatar_clear(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let result = api.call("avatar_clear", true, json!({}))?;
    out.line(out.t("Avatar removed.", "头像已移除。"));
    Ok(result)
}

// ---------------------------------------------------------------------------------------------
// Commands: find teammates

fn cmd_teammates_list(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let mut rows = arr(&api.rpc("participants_wall", false, json!({"p_limit": a.int("limit")}))?);
    if a.flag("looking") {
        rows.retain(|r| truthy(&r["looking_for_team"]) || truthy(&r["seeking"]));
    }
    out.table(&rows, &[("ID", "id"), (out.t("Name", "名字"), "name"), (out.t("Seeking", "寻找"), "seeking"),
        (out.t("Team", "队伍"), "team_name"), (out.t("About", "简介"), "blurb")]);
    Ok(Value::Array(rows))
}

fn cmd_teammates_contact(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let result = api.rpc("teammate_contact", false, json!({"p_id": a.str("user_id")}))?;
    out.line(&py_dumps(&result));
    Ok(result)
}

fn cmd_teammates_invite(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let result = api.rpc("send_team_invite", true, json!({"p_recipient": a.str("user_id")}))?;
    out.line(out.t("Invitation sent.", "邀请已发送。"));
    Ok(result)
}

fn cmd_teammates_visibility(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let me = public_me(&whoami(api)?);
    let state = a.str("state").unwrap_or_default();
    if state == "show" {
        let mut view = Map::new();
        for k in ["show_on_wall", "blurb", "contact", "seeking", "seeking_count", "looking_for_team"] {
            view.insert(k.into(), me[k].clone());
        }
        for (k, v) in &view {
            out.line(&format!("{:<18} {}", k, blank_none(v)));
        }
        return Ok(Value::Object(view));
    }
    let seeking = a.str("seeking").unwrap_or_else(|| if truthy(&me["seeking"]) { s(&me["seeking"]) } else { String::new() });
    let count = a.int("seeking_count").map(|c| json!(c)).unwrap_or_else(|| if truthy(&me["seeking_count"]) { me["seeking_count"].clone() } else { json!(1) });
    let pick = |opt: Option<String>, key: &str| opt.unwrap_or_else(|| if truthy(&me[key]) { s(&me[key]) } else { String::new() });
    let fields = obj(vec![
        ("show_on_wall", Value::Bool(state == "on")),
        ("blurb", Value::String(py_strip(&pick(a.str("blurb"), "blurb")).chars().take(160).collect())),
        ("contact", Value::String(py_strip(&pick(a.str("contact"), "contact")).chars().take(200).collect())),
        ("seeking", Value::String(seeking.clone())),
        ("seeking_count", if !seeking.is_empty() { count } else { json!(0) }),
        ("looking_for_team", Value::Bool(!seeking.is_empty())),
    ]);
    api.call("profile_update", true, json!({"fields": fields.clone()}))?;
    let on = state == "on";
    out.line(if on { out.t("You are now listed on Find teammates.", "你已出现在「找队友」页面。") }
        else { out.t("You are no longer listed on Find teammates.", "你已不在「找队友」页面显示。") });
    Ok(fields)
}

// ---------------------------------------------------------------------------------------------
// Commands: team

fn team_or_fail(me: &Value) -> R<Value> {
    if !truthy(&me["team"]) {
        return Err(CliError::new("need_team"));
    }
    Ok(me["team"].clone())
}

fn with_leader(members: &[Value]) -> Vec<Value> {
    members.iter().map(|m| {
        let mut m = m.clone();
        if let Value::Object(o) = &mut m {
            let star = if truthy(&o.get("is_leader").cloned().unwrap_or(Value::Null)) { "*" } else { "" };
            o.insert("leader".into(), Value::String(star.into()));
        }
        m
    }).collect()
}

fn cmd_team_show(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let me = whoami(api)?;
    let team = g(&me, "team");
    if !truthy(&team) {
        out.line(out.t("You are not on a team. Create one (survey26 team create NAME) or join one (survey26 team join CODE).",
            "你还没有队伍。可创建（survey26 team create 队名）或加入（survey26 team join 邀请码）。"));
        return Ok(json!({"team": null, "members": []}));
    }
    let members = arr(&api.rpc("team_members", false, json!({"p_team_id": team["id"]}))?);
    let info = public_me(&me)["team"].clone();
    out.line(&format!("{}  ({}/{}{})", py_none_str(&team["name"]), members.len(), py_none_str(&team["max_size"]),
        if truthy(&team["is_locked"]) { out.t(", locked", "，已锁定") } else { "" }));
    let code = py_none_str(&g(&team, "invite_code"));
    out.line(&format!("{}{}   {}/team?invite={}", out.t("Invite code: ", "邀请码："), code, SITE, code));
    out.table(&with_leader(&members), &[("ID", "id"), (out.t("Name", "名字"), "name"), (out.t("Captain", "队长"), "leader"), ("GitHub", "github")]);
    Ok(obj(vec![("team", info), ("members", Value::Array(members))]))
}

fn cmd_team_members(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let team = team_or_fail(&whoami(api)?)?;
    let members = arr(&api.rpc("team_members", false, json!({"p_team_id": team["id"]}))?);
    out.table(&with_leader(&members), &[("ID", "id"), (out.t("Name", "名字"), "name"), (out.t("Captain", "队长"), "leader"),
        ("GitHub", "github"), (out.t("Affiliation", "单位"), "affiliation")]);
    Ok(Value::Array(members))
}

fn cmd_team_create(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    api.rpc("create_team", true, json!({"p_name": py_strip(&a.str("name").unwrap_or_default()), "p_max_size": a.int("max_size"),
        "p_project_idea": py_strip(&a.str("idea").unwrap_or_default()), "p_github_repo": py_strip(&a.str("repo").unwrap_or_default())}))?;
    let team = public_me(&whoami(api)?)["team"].clone();
    out.line(&out.t("Team created: {0}. Invite code: {1}", "队伍已创建：{0}。邀请码：{1}")
        .replace("{0}", &py_none_str(&team["name"])).replace("{1}", &py_none_str(&team["invite_code"])));
    Ok(team)
}

fn cmd_team_join(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    api.rpc("join_team", true, json!({"p_invite_code": py_strip(&a.str("code").unwrap_or_default()).to_uppercase()}))?;
    let team = public_me(&whoami(api)?)["team"].clone();
    out.line(&out.t("You joined {0}.", "你已加入 {0}。").replace("{0}", &py_none_str(&g(&or_empty(team.clone()), "name"))));
    Ok(team)
}

fn cmd_team_leave(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    confirm(a, out, "Leave your team?", "确定退出队伍吗？")?;
    api.rpc("leave_team", true, json!({}))?;
    out.line(out.t("You left the team.", "你已退出队伍。"));
    Ok(json!({"left": true}))
}

fn cmd_team_set(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let team = team_or_fail(&whoami(api)?)?;
    let lock = match a.get("lock") { Some(Parsed::Bool(b)) => Value::Bool(*b), _ => g(&team, "is_locked") };
    let name = match a.str("name") {
        Some(n) if py_strip(&n) != s_or(&team["name"]) => Value::String(py_strip(&n)),
        _ => Value::Null,
    };
    let keep = |opt: Option<String>, key: &str| py_strip(&opt.unwrap_or_else(|| if truthy(&team[key]) { s(&team[key]) } else { String::new() }));
    api.rpc("update_team", true, obj(vec![
        ("p_project_idea", Value::String(keep(a.str("idea"), "project_idea"))),
        ("p_github_repo", Value::String(keep(a.str("repo"), "github_repo"))),
        ("p_max_size", a.int("max_size").map(|v| json!(v)).unwrap_or_else(|| team["max_size"].clone())),
        ("p_is_locked", Value::Bool(truthy(&lock))),
        ("p_name", name),
    ]))?;
    let team = public_me(&whoami(api)?)["team"].clone();
    out.line(out.t("Team saved.", "队伍信息已保存。"));
    Ok(team)
}

fn cmd_team_code(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    if a.flag("regenerate") {
        api.rpc("regenerate_invite_code", true, json!({}))?;
    }
    let team = team_or_fail(&whoami(api)?)?;
    let code = py_none_str(&team["invite_code"]);
    let link = format!("{}/team?invite={}", SITE, code);
    out.line(&format!("{}{}", out.t("Invite code: ", "邀请码："), code));
    out.line(&format!("{}{}", out.t("Invite link: ", "邀请链接："), link));
    Ok(obj(vec![("invite_code", team["invite_code"].clone()), ("invite_link", Value::String(link)), ("regenerated", Value::Bool(a.flag("regenerate")))]))
}

fn cmd_team_transfer(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    confirm(a, out, "Make this member the captain? You will no longer be the captain.", "确定把队长转给这位成员吗？转让后你将不再是队长。")?;
    api.rpc("transfer_leadership", true, json!({"p_user_id": a.str("user_id")}))?;
    out.line(out.t("Captain changed.", "已转让队长。"));
    Ok(json!({"leader_id": a.str("user_id")}))
}

fn cmd_team_kick(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    confirm(a, out, "Remove this member from the team?", "确定把这位成员移出队伍吗？")?;
    api.rpc("remove_member", true, json!({"p_user_id": a.str("user_id")}))?;
    out.line(out.t("Member removed.", "已移出该成员。"));
    Ok(json!({"removed": a.str("user_id")}))
}

fn cmd_team_disband(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    confirm(a, out, "Disband the team? This cannot be undone.", "确定解散队伍吗？此操作无法撤销。")?;
    api.rpc("disband_team", true, json!({}))?;
    out.line(out.t("Team disbanded.", "队伍已解散。"));
    Ok(json!({"disbanded": true}))
}

fn cmd_team_directory(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let rows = arr(&api.rpc("team_directory", false, json!({}))?);
    out.table(&rows, &[("ID", "id"), (out.t("Team", "队伍"), "name"), (out.t("Members", "人数"), "member_count"),
        (out.t("Max", "上限"), "max_size"), (out.t("Idea", "想法"), "project_idea")]);
    Ok(Value::Array(rows))
}

fn cmd_team_request(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let result = api.rpc("request_team_join", true, json!({"p_team_id": a.str("team_id")}))?;
    out.line(out.t("Request sent to the captain.", "已向队长发送申请。"));
    Ok(result)
}

fn cmd_team_capacity(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let result = api.rpc("team_capacity", false, json!({}))?;
    out.line(&py_dumps(&result));
    Ok(result)
}

// ---------------------------------------------------------------------------------------------
// Commands: invitations

fn cmd_invites_list(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let rows = arr(&api.rpc("my_team_invitations", false, json!({}))?);
    let received: Vec<&Value> = rows.iter().filter(|r| s_or(&r["direction"]) == "received").collect();
    if !received.is_empty() && !a.flag("keep_unread") {
        let latest = received.iter().map(|r| s_or(&r["updated_at"])).max().unwrap_or_default();
        let ids: Vec<Value> = received.iter().map(|r| r["id"].clone()).collect();
        let _ = api.rpc("mark_team_invitations_read", true, json!({"p_ids": ids, "p_through": latest}));
    }
    out.table(&rows, &[("ID", "id"), (out.t("Direction", "方向"), "direction"), (out.t("Kind", "类型"), "kind"),
        (out.t("Status", "状态"), "status"), (out.t("Team", "队伍"), "team_name"), (out.t("Updated", "更新时间"), "updated_at")]);
    Ok(Value::Array(rows))
}

fn cmd_invites_respond(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let accept = a.command.last().map(|c| c == "accept").unwrap_or(false);
    let result = api.rpc("respond_team_invite", true, json!({"p_invitation": a.str("invitation_id"), "p_accept": accept}))?;
    out.line(if accept { out.t("Accepted.", "已接受。") } else { out.t("Declined.", "已拒绝。") });
    Ok(result)
}

fn cmd_invites_cancel(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let result = api.rpc("cancel_team_invite", true, json!({"p_invitation": a.str("invitation_id")}))?;
    out.line(out.t("Cancelled.", "已撤回。"));
    Ok(result)
}

// ---------------------------------------------------------------------------------------------
// Commands: friends and invitations by UID

fn parse_uid(text: Option<String>) -> R<i64> {
    let text = py_strip(&text.unwrap_or_default());
    let ok = text.len() == 9 && text.chars().all(|c| c.is_ascii_digit()) && !text.starts_with('0');
    if !ok {
        return Err(CliError::exit("invalid_uid", "", EXIT_USAGE));
    }
    Ok(text.parse().unwrap())
}

/// The by-UID actions answer {"error": code} instead of failing; the CLI reports those as its errors.
fn uid_result(result: Value) -> R<Value> {
    let result = if result.is_object() { result } else { json!({}) };
    if truthy(&result["error"]) {
        let code = py_str(&result["error"]);
        let code = match code.as_str() { "self" => "uid_self".to_string(), "daily_limit" => "uid_daily_limit".to_string(), _ => code };
        return Err(CliError::new(&code));
    }
    Ok(result)
}

fn cmd_friends_list(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let data = or_empty(api.rpc("my_friends", false, json!({}))?);
    out.line(&format!("{}{}", out.t("Your UID: ", "你的 UID："), or_blank(&g(&data, "uid"))));
    let sections: [(&str, &str, Vec<(&str, &str)>); 4] = [
        (out.t("Friends", "好友"), "friends", vec![("UID", "uid"), (out.t("Name", "名字"), "name"), (out.t("Team", "队伍"), "team_name"), ("USER_ID", "user_id")]),
        (out.t("Requests to you", "收到的请求"), "incoming", vec![("ID", "id"), (out.t("Name", "名字"), "name"), ("USER_ID", "user_id"), (out.t("Sent", "时间"), "created_at")]),
        (out.t("Your pending requests", "你发出的请求"), "outgoing", vec![("ID", "id"), ("UID", "uid"), (out.t("Sent", "时间"), "created_at")]),
        (out.t("Blocked", "已屏蔽"), "blocked", vec![("USER_ID", "user_id"), (out.t("Name", "名字"), "name")]),
    ];
    for (title, key, columns) in sections.iter() {
        out.line("");
        out.line(title);
        out.table(&arr(&g(&data, key)), columns);
    }
    Ok(data)
}

fn cmd_friends_add(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let uid = parse_uid(a.str("uid"))?;
    let result = uid_result(api.rpc("send_friend_request", true, json!({"p_uid": uid}))?)?;
    let status = s_or(&result["status"]);
    out.line(match status.as_str() {
        "accepted" => out.t("You are now friends.", "你们已成为好友。"),
        "already_friends" => out.t("You are already friends.", "你们已经是好友了。"),
        _ => out.t("Friend request sent.", "好友请求已发送。"),
    });
    Ok(result)
}

fn cmd_friends_respond(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let accept = a.command.last().map(|c| c == "accept").unwrap_or(false);
    let result = api.rpc("respond_friend_request", true, json!({"p_request": a.str("request_id"), "p_accept": accept}))?;
    out.line(if accept { out.t("Accepted.", "已接受。") } else { out.t("Declined.", "已拒绝。") });
    Ok(result)
}

fn cmd_friends_cancel(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let result = api.rpc("cancel_friend_request", true, json!({"p_request": a.str("request_id")}))?;
    out.line(out.t("Cancelled.", "已撤回。"));
    Ok(result)
}

fn cmd_friends_remove(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let result = api.rpc("remove_friend", true, json!({"p_user": a.str("user_id")}))?;
    out.line(out.t("Removed from your friends.", "已删除好友。"));
    Ok(result)
}

fn cmd_friends_block(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let block = a.command.last().map(|c| c == "block").unwrap_or(false);
    let result = api.rpc(if block { "block_user" } else { "unblock_user" }, true, json!({"p_user": a.str("user_id")}))?;
    out.line(if block { out.t("Blocked.", "已屏蔽。") } else { out.t("Unblocked.", "已解除屏蔽。") });
    Ok(result)
}

fn cmd_team_invite_uid(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let uid = parse_uid(a.str("uid"))?;
    let result = uid_result(api.rpc("send_team_invite_by_uid", true, json!({"p_uid": uid}))?)?;
    out.line(out.t("Invitation sent.", "邀请已发送。"));
    Ok(result)
}

// ---------------------------------------------------------------------------------------------
// Commands: team variables and domains

fn print_env(out: &Out, env: &Value) {
    out.line(out.t("Variables:", "变量："));
    let rows: Vec<Value> = arr(&env["variables"]).iter().map(|v| {
        let secret = truthy(&v["secret"]);
        obj(vec![
            ("name", v["name"].clone()),
            ("kind", Value::String(if secret { out.t("secret", "密文") } else { out.t("plain", "明文") }.into())),
            ("value", if secret { g(v, "masked_value") } else { g(v, "value") }),
            ("flags", Value::String({
                let mut f: Vec<&str> = Vec::new();
                if truthy(&v["model"]) { f.push(out.t("model", "模型")); }
                if truthy(&v["disabled"]) { f.push(out.t("off", "已停用")); }
                f.join(" ")
            })),
            ("updated_at", g(v, "updated_at")),
        ])
    }).collect();
    out.table(&rows, &[(out.t("Name", "名称"), "name"), (out.t("Kind", "类型"), "kind"), (out.t("Value", "值"), "value"),
        (out.t("Flags", "标记"), "flags"), (out.t("Updated", "更新时间"), "updated_at")]);
    if truthy(&env["open"]) {
        out.line(out.t("Network: any public address over HTTPS (443) and HTTP (80); private and metadata addresses are unreachable; every destination is logged (no content). No domain list is needed.",
            "网络：可访问公网上的任何地址（HTTPS 443、HTTP 80 端口），内网和元数据地址不可访问；每个访问地址都会被记录（不含内容），无需登记域名。"));
    } else {
        out.line(&format!("{}{}", out.t("Allowed domains: ", "允许访问的域名："), domains_text(out, env)));
    }
    if truthy(&env["egress_route"]) {
        out.line(&route_text(out, &env["egress_route"]));
    }
}

fn route_text(out: &Out, route: &Value) -> String {
    let name = if truthy(&route["route"]) { py_str(&route["route"]) } else { "direct".to_string() };
    let label = match name.as_str() {
        "direct" => out.t("direct", "直连").to_string(),
        "cn" => out.t("China route", "回国代理").to_string(),
        "overseas" => out.t("overseas route", "海外代理").to_string(),
        other => other.to_string(),
    };
    let mut text = format!("{}{}", out.t("Egress route: ", "出网线路："), label);
    if name != "direct" {
        let fallback = route.get("auto_fallback").map(truthy).unwrap_or(true);
        let state = if fallback { out.t("on", "开") } else { out.t("off", "关") };
        text += &out.t(" (automatic fallback to direct: %s)", "（节点不可用时自动改为直连：%s）").replace("%s", state);
    }
    if !truthy(&route["available"]) {
        text += out.t(" - not offered right now; evaluations connect directly", "（暂未开放，评测直接连接）");
    }
    text
}

fn domains_text(out: &Out, env: &Value) -> String {
    let d: Vec<String> = arr(&env["domains"]).iter().map(py_str).collect();
    if d.is_empty() { out.t("(none)", "（无）").to_string() } else { d.join(", ") }
}

fn env_view(env: Value) -> Value {
    let mut env = or_empty(env);
    let vars: Vec<Value> = arr(&env["variables"]).iter().map(|v| {
        let secret = truthy(&v["secret"]);
        let masked = if secret && truthy(&v["hint"]) { Value::String(format!("****{}", py_str(&v["hint"]))) }
            else if secret { Value::String("****".into()) } else { Value::Null };
        obj(vec![("name", g(v, "name")), ("secret", Value::Bool(secret)), ("masked_value", masked),
            ("value", if secret { Value::Null } else { g(v, "value") }), ("updated_at", g(v, "updated_at")),
            ("model", Value::Bool(truthy(&v["model"]))), ("disabled", Value::Bool(truthy(&v["disabled"])))])
    }).collect();
    if let Value::Object(o) = &mut env {
        o.insert("variables".into(), Value::Array(vars));
    }
    env
}

fn team_environment(v: Value) -> Value {
    env_view(or_empty(g(&or_empty(v), "team_environment")))
}

fn cmd_env_show(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let env = team_environment(api.portal("team_environment", false, json!({}))?);
    print_env(out, &env);
    Ok(env)
}

fn cmd_env_set(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let value_arg = a.str("value");
    let from_env = a.str("from_env");
    let sources = [value_arg.is_some(), a.flag("value_stdin"), from_env.is_some()].iter().filter(|x| **x).count();
    if sources > 1 {
        return Err(CliError::exit("usage", "use one of VALUE, --value-stdin, --from-env", EXIT_USAGE));
    }
    let value = if a.flag("value_stdin") {
        read_all_stdin().trim_end_matches('\n').to_string()
    } else if let Some(var) = from_env {
        match std::env::var(&var) {
            Ok(v) => v,
            Err(_) => return Err(CliError::exit("usage", format!("environment variable {} is not set", var), EXIT_USAGE)),
        }
    } else if let Some(v) = value_arg {
        v
    } else if interactive(a) {
        read_hidden(out.t("Value (hidden): ", "变量值（不显示）："))
    } else {
        return Err(CliError::exit("usage", "give the value: --value-stdin (recommended for secrets), --from-env VAR or VALUE", EXIT_USAGE));
    };
    let name = a.str("name").unwrap_or_default();
    let env = team_environment(api.portal("save_team_variable", true, json!({"name": name, "value": value, "secret": !a.flag("plain")}))?);
    out.line(&out.t("Saved {0}.", "已保存 {0}。").replace("{0}", &name));
    Ok(env)
}

fn normalize_prefix(raw: &str) -> String {
    let upper = py_strip(raw).to_uppercase();
    let mut value = String::new();
    let mut in_bad = false;
    for ch in upper.chars() {
        if ch.is_ascii_uppercase() || ch.is_ascii_digit() || ch == '_' {
            value.push(ch);
            in_bad = false;
        } else if !in_bad {
            value.push('_');
            in_bad = true;
        }
    }
    let value: String = value.trim_start_matches(|c: char| !c.is_ascii_uppercase()).trim_end_matches('_').to_string();
    value.chars().take(40).collect()
}

fn cmd_env_model(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let presets: Value = serde_json::from_str(MODEL_PROVIDERS).expect("modelProviders.json");
    let wanted = a.str("provider").unwrap_or_default();
    let preset = arr(&presets).into_iter().find(|p| s_or(&p["cli"]) == wanted).ok_or_else(|| CliError::new("usage"))?;
    let prefix_arg = a.str("prefix");
    let prefix = normalize_prefix(&prefix_arg.clone().unwrap_or_default());
    if prefix_arg.is_some() && prefix.is_empty() {
        return Err(CliError::exit("usage", "--prefix needs a letter, e.g. KIMI", EXIT_USAGE));
    }
    let base = if !prefix.is_empty() { prefix } else if s_or(&preset["protocol"]) == "anthropic" { "ANTHROPIC".into() } else { "OPENAI".into() };
    let (key_name, url_name, model_name) = (format!("{}_API_KEY", base), format!("{}_BASE_URL", base), format!("{}_MODEL", base));
    let key_arg = a.str("key").unwrap_or_default();
    let key = py_strip(&if key_arg == "-" { read_all_stdin() } else { key_arg });
    if key.is_empty() {
        return Err(CliError::exit("usage", "give the API key with --key KEY, or --key - to read it from standard input", EXIT_USAGE));
    }
    let base_url = py_strip(&a.str("base_url").unwrap_or_else(|| s_or(&preset["baseUrl"])));
    let valid_url = base_url.starts_with("https://") && base_url.len() > 8 && !base_url.chars().any(char::is_whitespace);
    if !valid_url {
        let tail = if s_or(&preset["baseUrl"]).is_empty() { " (required for --provider custom)" } else { "" };
        return Err(CliError::exit("usage", format!("--base-url must be an https:// address{}", tail), EXIT_USAGE));
    }
    let model = py_strip(&a.str("model").unwrap_or_else(|| s_or(&preset["model"])));
    let env = or_empty(g(&or_empty(api.portal("team_environment", false, json!({}))?), "team_environment"));
    let existing: Vec<String> = arr(&env["variables"]).iter().map(|v| s_or(&v["name"])).collect();
    let mut writes: Vec<(String, String, bool)> = vec![(key_name.clone(), key, true), (url_name.clone(), base_url.clone(), false)];
    if !model.is_empty() {
        writes.push((model_name.clone(), model.clone(), false));
    }
    let deletes: Vec<String> = if model.is_empty() && existing.contains(&model_name) { vec![model_name.clone()] } else { vec![] };
    let mut taken: Vec<String> = writes.iter().filter(|w| existing.contains(&w.0)).map(|w| w.0.clone()).collect();
    taken.extend(deletes.iter().cloned());
    if !taken.is_empty() && !a.flag("replace") {
        return Err(CliError::detail("variables_exist", out.t("Already set: {0}. Pass --replace to overwrite them, or --prefix NAME to add another service.",
            "已存在：{0}。如需覆盖请加 --replace，或用 --prefix 名称 添加另一个服务。").replace("{0}", &taken.join(", "))));
    }
    let added = writes.iter().filter(|w| !existing.contains(&w.0)).count();
    if let Some(limit) = env["limits"]["variables"].as_i64() {
        if (existing.len() + added) as i64 > limit {
            return Err(CliError::new("team_variable_limit"));
        }
    }
    let mut last = json!({});
    for (name, value, secret) in &writes {
        last = or_empty(api.portal("save_team_variable", true, json!({"name": name, "value": value, "secret": secret, "model": true}))?);
    }
    for name in &deletes {
        last = or_empty(api.portal("delete_team_variable", true, json!({"name": name}))?);
    }
    let label = s_or(&preset["label"]);
    let model_part = if model.is_empty() { String::new() } else { format!("{}{}", out.t(", ", "、"), model_name) };
    out.line(&out.t("Saved model service {0}: {1} (secret), {2}{3}.", "已保存模型服务 {0}：{1}（密文）、{2}{3}。")
        .replace("{0}", &label).replace("{1}", &key_name).replace("{2}", &url_name).replace("{3}", &model_part));
    Ok(obj(vec![
        ("provider", preset["cli"].clone()), ("label", preset["label"].clone()), ("protocol", preset["protocol"].clone()),
        ("variables", obj(vec![("key", json!(key_name)), ("base_url", json!(url_name)), ("model", json!(model_name))])),
        ("base_url", json!(base_url)), ("model", if model.is_empty() { Value::Null } else { json!(model) }),
        ("saved", Value::Array(writes.iter().map(|w| json!(w.0)).collect())),
        ("deleted", Value::Array(deletes.iter().map(|d| json!(d)).collect())),
        ("team_environment", env_view(or_empty(g(&last, "team_environment")))),
    ]))
}

fn env_flags(api: &mut Api, name: &str, flags: Value) -> R<Value> {
    let mut body = Map::new();
    body.insert("name".into(), json!(name));
    for (k, v) in flags.as_object().unwrap() {
        body.insert(k.clone(), v.clone());
    }
    Ok(team_environment(api.portal("set_team_variable_flags", true, Value::Object(body))?))
}

fn cmd_env_disable(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let name = a.str("name").unwrap_or_default();
    let env = env_flags(api, &name, json!({"disabled": true}))?;
    out.line(&out.t("Switched off {0}: kept, but not given to your program in evaluations.", "已停用 {0}：保留，但评测时不提供给程序。").replace("{0}", &name));
    Ok(env)
}

fn cmd_env_enable(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let name = a.str("name").unwrap_or_default();
    let env = env_flags(api, &name, json!({"disabled": false}))?;
    out.line(&out.t("Switched on {0}.", "已启用 {0}。").replace("{0}", &name));
    Ok(env)
}

fn cmd_env_tag(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let name = a.str("name").unwrap_or_default();
    let model = a.str("tag").as_deref() == Some("model");
    let env = env_flags(api, &name, json!({"model": model}))?;
    let text = if model {
        out.t("{0} is model-related: left out of evaluations without a model (eval start --no-model).",
            "{0} 标记为模型相关：在不提供模型的评测（eval start --no-model）中不提供。")
    } else {
        out.t("{0} is not model-related: given to every evaluation.", "{0} 不再标记为模型相关：所有评测都会提供。")
    };
    out.line(&text.replace("{0}", &name));
    Ok(env)
}

fn cmd_env_unset(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let name = a.str("name").unwrap_or_default();
    let env = team_environment(api.portal("delete_team_variable", true, json!({"name": name}))?);
    out.line(&out.t("Deleted {0}.", "已删除 {0}。").replace("{0}", &name));
    Ok(env)
}

fn cmd_env_domains(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let sub = if a.command.len() > 2 { a.command[2].clone() } else { String::new() };
    if sub.is_empty() || sub == "list" {
        let env = team_environment(api.portal("team_environment", false, json!({}))?);
        out.line(&domains_text(out, &env));
        return Ok(json!({"domains": if truthy(&env["domains"]) { env["domains"].clone() } else { json!([]) }}));
    }
    let hosts = if sub == "clear" { vec![] } else { a.list("hosts") };
    let env = team_environment(api.portal("set_team_domains", true, json!({"domains": hosts}))?);
    out.line(&format!("{}{}", out.t("Allowed domains: ", "允许访问的域名："), domains_text(out, &env)));
    Ok(json!({"domains": if truthy(&env["domains"]) { env["domains"].clone() } else { json!([]) }}))
}

fn cmd_env_route(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let fallback = match a.get("fallback") { Some(Parsed::Bool(b)) => Some(*b), _ => None };
    let mut env = Value::Null;
    let route = match a.str("route") {
        Some(r) => r,
        None => {
            env = or_empty(g(&or_empty(api.portal("team_environment", false, json!({}))?), "team_environment"));
            let r = &env["egress_route"]["route"];
            if truthy(r) { py_str(r) } else { "direct".to_string() }
        }
    };
    if a.str("route").is_some() || fallback.is_some() {
        let fields = match fallback {
            None => json!({"route": route}),
            Some(f) => json!({"route": route, "auto_fallback": f}),
        };
        env = or_empty(g(&or_empty(api.portal("set_team_egress_route", true, fields)?), "team_environment"));
    }
    let route = if truthy(&env["egress_route"]) { env["egress_route"].clone() }
        else { json!({"available": false, "route": "direct", "auto_fallback": true}) };
    out.line(&route_text(out, &route));
    Ok(json!({"egress_route": route}))
}

// ---------------------------------------------------------------------------------------------
// Commands: projects and versions

fn revision_view(r: &Value, batches: &[Value]) -> Value {
    let archived = truthy(&r["archived_at"]);
    let evaluations = batches.iter().filter(|b| g(b, "revision_id") == r["id"]).count();
    obj(vec![
        ("revision_id", r["id"].clone()),
        ("title", g(r, "title")),
        ("status", if archived { Value::String("withdrawn".into()) } else { g(r, "status") }),
        ("raw_status", g(r, "status")),
        ("withdrawn", Value::Bool(archived)),
        ("source_kind", g(r, "source_kind")),
        ("source_location", g(r, "source_location")),
        ("source_digest", g(r, "source_digest")),
        ("created_at", g(r, "created_at")),
        ("approved_at", g(r, "approved_at")),
        ("error", if truthy(&r["error"]) { r["error"].clone() } else { Value::String(String::new()) }),
        ("public_test", if truthy(&r["public_test"]) { r["public_test"].clone() } else { json!({}) }),
        ("evaluations", json!(evaluations)),
    ])
}

fn cmd_project_list(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let batches = arr(&data["batches"]);
    let rows: Vec<Value> = all_revisions(&data).iter().filter(|r| a.flag("all") || !truthy(&r["archived_at"]))
        .map(|r| revision_view(r, &batches)).collect();
    let table: Vec<Value> = rows.iter().map(|r| with(r, "id", Value::String(s(&r["revision_id"]).chars().take(8).collect()))).collect();
    out.table(&table, &[("ID", "id"), (out.t("Project", "项目"), "title"), (out.t("Status", "状态"), "status"),
        (out.t("Source", "来源"), "source_kind"), (out.t("Evaluated", "已评测"), "evaluations"), (out.t("Created", "创建时间"), "created_at")]);
    Ok(Value::Array(rows))
}

fn recent_duplicate(data: &Value, title: &str, url: Option<&str>) -> bool {
    let t = now();
    all_revisions(data).iter().any(|r| {
        let created = parse_time(&s_or(&r["created_at"]));
        matches!(created, Some(c) if c != 0.0 && t - c < 600.0) && s_or(&r["title"]) == title
            && url.map_or(true, |u| r.get("source_location").and_then(|v| v.as_str()) == Some(u))
    })
}

const DUPLICATE_EN: &str = "You submitted the same project a few minutes ago. Submit it again? This uses one of today’s project preparations.";
const DUPLICATE_ZH: &str = "几分钟前刚提交过相同的项目。确定再提交一次吗？这会占用今天的 1 次项目准备机会。";

fn queued_line(out: &Out, result: &Value) {
    let rid = py_none_str(&g(result, "revision_id"));
    out.line(&out.t("Project queued for preparation: version {0}. Wait with: survey26 project wait {1}",
        "项目已排队，等待准备：版本 {0}。可用 survey26 project wait {1} 等待。")
        .replace("{0}", &rid).replace("{1}", &rid.chars().take(8).collect::<String>()));
}

fn cmd_project_submit_repo(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let mut url = py_strip(&a.str("url").unwrap_or_default()).trim_end_matches('/').to_string();
    if url.ends_with(".git") {
        url.truncate(url.len() - 4);
    }
    let title = py_strip(&a.str("title").filter(|t| !t.is_empty()).unwrap_or_else(|| url.rsplit('/').next().unwrap_or("").to_string()));
    if recent_duplicate(&portal_list(api)?, &title, Some(&url)) {
        confirm(a, out, DUPLICATE_EN, DUPLICATE_ZH)?;
    }
    // A branch/tag/commit or folder may also be part of the link (.../tree/<branch>/<folder>).
    let mut fields = json!({"title": title, "url": url});
    for key in ["branch", "subdir"] {
        let v = py_strip(&a.str(key).unwrap_or_default());
        if !v.is_empty() {
            fields[key] = Value::String(v);
        }
    }
    let result = or_empty(api.portal("submit_repository", true, fields)?);
    let commit = py_none_str(&g(&result, "source_commit"));
    if truthy(&g(&result, "source_commit")) {
        let extra: String = ["source_ref", "source_subdir"].iter().filter(|k| truthy(&g(&result, k)))
            .map(|k| format!(" · {}", py_none_str(&g(&result, k)))).collect();
        out.line(&out.t("Saved commit {0}{1}.", "已记录 commit {0}{1}。")
            .replace("{0}", &commit.chars().take(12).collect::<String>()).replace("{1}", &extra));
    }
    queued_line(out, &result);
    Ok(result)
}

fn cmd_project_upload(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let path = PathBuf::from(a.str("zip").unwrap_or_default());
    if !path.is_file() {
        return Err(CliError::new("file_not_found"));
    }
    let fname = path.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    if !fname.to_lowercase().ends_with(".zip") {
        return Err(CliError::new("wrong_file_type"));
    }
    let size = std::fs::metadata(&path).map(|m| m.len()).unwrap_or(0);
    if size == 0 || size > 50 * 1024 * 1024 {
        return Err(CliError::new("file_too_large"));
    }
    let bytes = std::fs::read(&path).map_err(|_| CliError::new("file_not_found"))?;
    if zip::ZipArchive::new(std::io::Cursor::new(&bytes)).is_err() {
        return Err(CliError::detail("wrong_file_type", "not a ZIP archive"));
    }
    let stem = path.file_stem().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
    let title = py_strip(&a.str("title").filter(|t| !t.is_empty()).unwrap_or(stem));
    if recent_duplicate(&portal_list(api)?, &title, None) {
        confirm(a, out, DUPLICATE_EN, DUPLICATE_ZH)?;
    }
    let slot = or_empty(api.portal("upload", true, json!({"purpose": "source"}))?);
    out.line(&out.t("Uploading {0} ({1} bytes)…", "正在上传 {0}（{1} 字节）…").replace("{0}", &fname).replace("{1}", &size.to_string()));
    let boundary = format!("----survey26{}", random_hex(8));
    let mut body = format!("--{b}\r\nContent-Disposition: form-data; name=\"cacheControl\"\r\n\r\n3600\r\n--{b}\r\nContent-Disposition: form-data; name=\"\"; filename=\"{f}\"\r\nContent-Type: application/zip\r\n\r\n",
        b = boundary, f = fname.replace('"', "")).into_bytes();
    body.extend_from_slice(&bytes);
    body.extend_from_slice(format!("\r\n--{}--\r\n", boundary).as_bytes());
    let key = py_str(&slot["apikey"]);
    http_put(&py_str(&slot["upload_url"]), &body, &[("apikey", key.clone()), ("Authorization", format!("Bearer {}", key)),
        ("x-upsert", "false".into()), ("Content-Type", format!("multipart/form-data; boundary={}", boundary))])?;
    let mut result = Value::Null;
    for attempt in 0..5 {
        match api.portal("submit_zip", true, json!({"title": title, "upload_id": slot["id"]})) {
            Ok(v) => {
                result = v;
                break;
            }
            Err(e) => {
                if e.code != "upload_not_finished" || attempt == 4 {
                    return Err(e);
                }
                sleep_secs((2 + attempt * 2) as f64);
            }
        }
    }
    let mut result = or_empty(result);
    queued_line(out, &result);
    if let Value::Object(o) = &mut result {
        o.insert("upload_id".into(), slot["id"].clone());
        o.insert("bytes".into(), json!(size));
    }
    Ok(result)
}

fn cmd_project_show(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let r = find_revision(&data, &a.str("revision").unwrap_or_default())?;
    let mut view = revision_view(&r, &arr(&data["batches"]));
    if let Value::Object(o) = &mut view {
        o.insert("manifest".into(), g(&r, "manifest"));
        o.insert("adapter_files".into(), if truthy(&r["adapter_files"]) { r["adapter_files"].clone() } else { json!({}) });
        o.insert("explanation".into(), if truthy(&r["explanation"]) { r["explanation"].clone() } else { json!("") });
        o.insert("approval_digest".into(), g(&r, "approval_digest"));
        o.insert("evidence".into(), g(&r, "observer_evidence"));
    }
    for key in ["revision_id", "title", "status", "source_kind", "source_location", "source_digest", "created_at", "approved_at"] {
        out.line(&format!("{:<16} {}", key, if truthy(&view[key]) { py_str(&view[key]) } else { String::new() }));
    }
    if truthy(&view["error"]) {
        out.line(&format!("{}{}", out.t("Error: ", "错误："), py_str(&view["error"])));
    }
    if truthy(&view["public_test"]) {
        out.line(&format!("{}{}", out.t("Public test: ", "公开场景测试："), py_dumps(&view["public_test"])));
    }
    if a.flag("files") || s_or(&view["status"]) == "reviewable" {
        out.line("");
        out.line(&format!("{}\n{}", out.t("Execution settings:", "运行设置："), py_dumps_indent(&view["manifest"])));
        if truthy(&view["explanation"]) {
            out.line(&format!("{}\n{}", out.t("Adapter explanation:", "适配说明："), py_str(&view["explanation"])));
        }
        let files = view["adapter_files"].as_object().cloned().unwrap_or_default();
        if !files.is_empty() {
            out.line(out.t("Added adapter files:", "新增的适配文件："));
            for (name, content) in &files {
                let text = if a.flag("files") { py_str(content) } else { format!("({} chars; add --files to print)", py_str(content).chars().count()) };
                out.line(&format!("--- {}\n{}", name, text));
            }
        } else {
            out.line(out.t("This project supplies its own interface; no adapter files were added.", "项目自带接口，没有新增适配文件。"));
        }
    }
    Ok(view)
}

fn cmd_project_wait(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let deadline = now() + a.int("timeout").unwrap_or(1800) as f64;
    let mut last: Option<Value> = None;
    loop {
        let data = polled_list(api, deadline)?;
        let r = find_revision(&data, &a.str("revision").unwrap_or_default())?;
        let status = g(&r, "status");
        if last.as_ref() != Some(&status) {
            out.line(&format!("{}  {}", time_hms(), py_none_str(&status)));
            if !out.json && s_or(&status) == "failed" {
                out.line(&if truthy(&r["error"]) { py_str(&r["error"]) } else { String::new() });
            }
            last = Some(status.clone());
        }
        if !REVISION_PENDING.contains(&s_or(&status).as_str()) {
            let view = revision_view(&r, &arr(&data["batches"]));
            if s_or(&status) == "failed" {
                let err = if truthy(&r["error"]) { py_str(&r["error"]) } else { "preparation failed".into() };
                return Err(CliError::exit("preparation_failed", err, EXIT_FAILED));
            }
            return Ok(view);
        }
        if now() >= deadline {
            return Err(CliError::new("wait_timeout"));
        }
        sleep_secs(std::cmp::max(5, a.int("interval").unwrap_or(15)) as f64);
    }
}

fn cmd_project_logs(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let r = find_revision(&data, &a.str("revision").unwrap_or_default())?;
    let diagnostics = api.portal("diagnostics", false, json!({"revision_id": r["id"]}))?;
    let diagnostics = if truthy(&diagnostics) { diagnostics } else { json!([]) };
    let test_run = g(&or_empty(g(&r, "public_test")), "run_id");
    let mut agent = Value::Null;
    if truthy(&test_run) {
        agent = api.portal("agent_log", false, json!({"run_id": test_run, "full": a.flag("full")})).unwrap_or(Value::Null);
    }
    for d in arr(&diagnostics) {
        out.line(&format!("== {} · {} · {} {}", py_none_str(&g(&d, "kind")), py_none_str(&g(&d, "status")),
            or_blank(&d["code"]), or_blank(&d["finished_at"])));
        if truthy(&d["log"]) {
            out.line(&py_str(&d["log"]));
        }
    }
    if truthy(&agent) && truthy(&agent["available"]) {
        out.line(out.t("== agent.log of the public test run", "== 公开测试运行的 agent.log"));
        out.line(&or_blank(&agent["log"]));
    }
    Ok(obj(vec![("revision_id", r["id"].clone()), ("diagnostics", diagnostics), ("public_test_run_id", test_run), ("agent_log", agent)]))
}

fn cmd_project_confirm(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let r = find_revision(&data, &a.str("revision").unwrap_or_default())?;
    if s_or(&r["status"]) != "reviewable" || truthy(&r["archived_at"]) {
        return Err(CliError::new("revision_not_reviewable"));
    }
    let short: String = s(&r["id"]).chars().take(8).collect();
    confirm(a, out, &format!("I reviewed the execution settings and adapter code (survey26 project show {} --files), and confirm this exact version.", short),
        &format!("我已检查运行设置和适配代码（survey26 project show {} --files），确认使用这个版本。", short))?;
    api.portal("approve", true, json!({"revision_id": r["id"], "digest": or_blank(&r["approval_digest"])}))?;
    out.line(&out.t("Version confirmed. Start an evaluation with: survey26 eval start {0}", "已确认版本。可用 survey26 eval start {0} 开始评测。").replace("{0}", &short));
    Ok(obj(vec![("revision_id", r["id"].clone()), ("confirmed", Value::Bool(true))]))
}

fn cmd_project_withdraw(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let r = find_revision(&data, &a.str("revision").unwrap_or_default())?;
    confirm(a, out, "Withdraw this version? It will be hidden and can no longer be confirmed or evaluated. The upload still counts toward today’s project preparations.",
        "撤回这个版本？撤回后它会被隐藏，不能再确认或评测；已用的上传次数不退回。")?;
    api.portal("withdraw", true, json!({"revision_id": r["id"]}))?;
    out.line(out.t("Version withdrawn.", "已撤回。"));
    Ok(obj(vec![("revision_id", r["id"].clone()), ("withdrawn", Value::Bool(true))]))
}

fn write_file(path: &Path, content: &[u8]) -> R<()> {
    std::fs::write(path, content).map_err(|e| CliError::detail("write_failed", e.to_string()))
}

fn cmd_project_download(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let r = find_revision(&data, &a.str("revision").unwrap_or_default())?;
    let url = py_str(&g(&or_empty(api.portal("download_project", false, json!({"revision_id": r["id"]}))?), "url"));
    let target = a.str("output").filter(|o| !o.is_empty()).unwrap_or_else(|| format!("project-{}.zip", s(&r["id"]).chars().take(8).collect::<String>()));
    let content = http_get(&url)?;
    write_file(Path::new(&target), &content)?;
    out.line(&format!("{}{}", out.t("Saved ", "已保存 "), target));
    Ok(obj(vec![("revision_id", r["id"].clone()), ("path", Value::String(target)), ("bytes", json!(content.len()))]))
}

fn cmd_project_evidence(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let r = find_revision(&data, &a.str("revision").unwrap_or_default())?;
    let notes = match a.str("notes") {
        Some(n) if n == "-" => read_all_stdin(),
        Some(n) => n,
        None => String::new(),
    };
    api.portal("evidence", true, json!({"revision_id": r["id"], "notes": notes, "code_url": a.str("code_url").unwrap_or_default()}))?;
    out.line(out.t("Saved.", "已保存。"));
    Ok(obj(vec![("revision_id", r["id"].clone()), ("saved", Value::Bool(true))]))
}

// ---------------------------------------------------------------------------------------------
// Commands: evaluations and results

fn quota_for(data: &Value, phase_id: &Value) -> Option<Value> {
    arr(&data["quota"]).into_iter().find(|q| &g(q, "phase_id") == phase_id)
}

const NO_MODEL_NOTE: (&str, &str) = (
    "Without a model: your program gets none of the team variables marked model-related, and OBSERVER_MODEL_DISABLED=1.",
    "本次不提供模型：程序拿不到标记为模型相关的队伍变量，并会收到 OBSERVER_MODEL_DISABLED=1。");

fn cmd_eval_start(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let r = find_revision(&data, &a.str("revision").unwrap_or_default())?;
    let phase = competition_phase(api, &data, a.str("phase").as_deref())?;
    let quota = quota_for(&data, &phase["phase_id"]);
    let repeat = arr(&data["batches"]).iter().any(|b| g(b, "revision_id") == r["id"] && g(b, "phase_id") == phase["phase_id"] && !truthy(&b["quota_refunded"]));
    if repeat {
        let (left_en, left_zh) = match &quota {
            Some(q) => (format!(" ({} left today)", py_none_str(&q["remaining"])), format!("（今天还剩 {} 次）", py_none_str(&q["remaining"]))),
            None => (String::new(), String::new()),
        };
        confirm(a, out, &format!("This version has already been evaluated. Evaluating it again uses one more of today’s evaluations{}. Continue?", left_en),
            &format!("这个版本已经评测过。再评测一次会再占用今天 1 次评测{}。确定继续吗？", left_zh))?;
    }
    let no_model = a.flag("no_model");
    let mut fields = obj(vec![("phase_id", phase["phase_id"].clone()), ("revision_id", r["id"].clone())]);
    if no_model {
        fields = with(&fields, "no_model", Value::Bool(true));
    }
    let first = if repeat { with(&fields, "confirm_repeat", Value::Bool(true)) } else { fields.clone() };
    let result = match api.portal("evaluate", true, first) {
        Ok(v) => v,
        Err(e) => {
            if repeat || e.code != "revision_already_evaluated" {
                return Err(e);
            }
            confirm(a, out, "This version has already been evaluated. Evaluate it again?", "这个版本已经评测过。确定再评测一次吗？")?;
            let mut again = Map::new();
            again.insert("confirm_repeat".into(), Value::Bool(true));
            for (k, v) in fields.as_object().unwrap() {
                again.insert(k.clone(), v.clone());
            }
            api.portal("evaluate", true, Value::Object(again))?
        }
    };
    let batch_id = g(&or_empty(result), "batch_id");
    let bid = py_none_str(&batch_id);
    out.line(&out.t("Evaluation queued: {0} (phase {1}). Wait with: survey26 eval wait {2}", "已加入评测队列：{0}（赛程 {1}）。可用 survey26 eval wait {2} 等待。")
        .replace("{0}", &bid).replace("{1}", &py_none_str(&phase["slug"])).replace("{2}", &bid.chars().take(8).collect::<String>()));
    if no_model {
        out.line(out.t(NO_MODEL_NOTE.0, NO_MODEL_NOTE.1));
    }
    Ok(obj(vec![("batch_id", batch_id), ("phase_id", phase["phase_id"].clone()), ("phase", phase["slug"].clone()),
        ("revision_id", r["id"].clone()), ("repeat", Value::Bool(repeat)), ("model_disabled", Value::Bool(no_model))]))
}

fn cmd_eval_selfcheck(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let r = find_revision(&data, &a.str("revision").unwrap_or_default())?;
    let phase = competition_phase(api, &data, a.str("phase").as_deref())?;
    let quota = quota_for(&data, &phase["phase_id"]);
    if let Some(q) = &quota {
        if let Some(rem) = q["remaining"].as_f64() {
            if rem < SELF_CHECK_RUNS as f64 {
                return Err(CliError::new("repeat_daily_limit"));
            }
        }
    }
    let remaining = quota.as_ref().map(|q| py_none_str(&q["remaining"])).unwrap_or_else(|| "?".into());
    confirm(a, out, &format!("Evaluate this version 3 times in a row? This uses 3 of today’s evaluations ({} left today).", remaining),
        &format!("将对此版本连续评测 3 次，占用今天 3 次评测（今天还剩 {} 次）。确定继续吗？", remaining))?;
    let no_model = a.flag("no_model");
    let mut params = json!({"p_phase": phase["phase_id"], "p_revision": r["id"], "p_confirm_repeat": true});
    if no_model {
        params = with(&params, "p_no_model", Value::Bool(true));
    }
    let result = api.rpc("observer_create_repeat_batches", true, params)?;
    out.line(out.t("Queued: the 3 evaluations run one after another.", "已加入评测队列，3 次评测将依次进行。"));
    if no_model {
        out.line(out.t(NO_MODEL_NOTE.0, NO_MODEL_NOTE.1));
    }
    Ok(obj(vec![("result", result), ("phase_id", phase["phase_id"].clone()), ("phase", phase["slug"].clone()), ("revision_id", r["id"].clone()),
        ("model_disabled", Value::Bool(no_model))]))
}

fn cmd_eval_list(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let names = scenario_names(api, &data)?;
    let limit = a.int("limit").unwrap_or(20);
    let batches = arr(&data["batches"]);
    let rows: Vec<Value> = py_slice(&batches, limit).iter().map(|b| batch_summary(b, &names, &out.lang)).collect();
    let titles: Map<String, Value> = all_revisions(&data).iter().map(|r| (s(&r["id"]), g(r, "title"))).collect();
    let table: Vec<Value> = rows.iter().map(|b| {
        let mut t = b.clone();
        let o = t.as_object_mut().unwrap();
        o.insert("id".into(), Value::String(s(&b["batch_id"]).chars().take(8).collect()));
        o.insert("version".into(), Value::String(s_or(&b["revision_id"]).chars().take(8).collect()));
        o.insert("project".into(), titles.get(&s_or(&b["revision_id"])).cloned().unwrap_or(Value::Null));
        o.insert("score_text".into(), Value::String(fmt_score(&b["score"])));
        o.insert("self_check".into(), Value::String(if truthy(&b["repeat_group"]) { "3x".into() } else { String::new() }));
        o.insert("no_model".into(), Value::String(if truthy(&b["model_disabled"]) { out.t("no model", "无模型").into() } else { String::new() }));
        o.insert("counted".into(), Value::String(if truthy(&b["quota_refunded"]) { out.t("not counted", "未计次").into() } else { String::new() }));
        t
    }).collect();
    out.table(&table, &[("ID", "id"), (out.t("Status", "状态"), "status"), (out.t("Score", "分数"), "score_text"),
        (out.t("Version", "版本"), "version"), (out.t("Project", "项目"), "project"), ("", "self_check"), ("", "no_model"), ("", "counted"),
        (out.t("Created", "创建时间"), "created_at")]);
    Ok(Value::Array(rows))
}

fn spread(values: &[f64]) -> Value {
    if values.is_empty() {
        return Value::Null;
    }
    let sum: f64 = values.iter().fold(0.0, |acc, v| acc + v);
    let min = values.iter().cloned().fold(f64::INFINITY, f64::min);
    let max = values.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    obj(vec![("mean", json!(sum / values.len() as f64)), ("min", json!(min)), ("max", json!(max))])
}

fn repeat_summary(data: &Value, group: &Value) -> Value {
    let own: Vec<Value> = arr(&data["batches"]).into_iter().filter(|b| &g(b, "repeat_group") == group).collect();
    if own.is_empty() {
        return Value::Null;
    }
    let scored: Vec<&Value> = own.iter().filter(|b| s_or(&b["status"]) == "scored").collect();
    let mut cards: Vec<(String, Value, Vec<f64>)> = Vec::new();
    for b in &scored {
        for run in arr(&b["observer_runs"]) {
            if let Some(score) = run["score"].as_f64() {
                let key = py_dumps(&run["scenario_id"]);
                match cards.iter_mut().find(|c| c.0 == key) {
                    Some(c) => c.2.push(score),
                    None => cards.push((key, run["scenario_id"].clone(), vec![score])),
                }
            }
        }
    }
    let card_rows: Vec<Value> = cards.iter().map(|(_, id, v)| with(&spread(v), "scenario_id", id.clone())).collect();
    let overall: Vec<f64> = scored.iter().filter_map(|b| b["score"].as_f64()).collect();
    let runs = if truthy(&own[0]["repeat_runs"]) { own[0]["repeat_runs"].clone() } else { json!(SELF_CHECK_RUNS) };
    obj(vec![("group", group.clone()), ("runs", runs), ("scored", json!(scored.len())),
        ("active", json!(own.iter().filter(|b| ACTIVE.contains(&s_or(&b["status"]).as_str())).count())),
        ("cards", Value::Array(card_rows)), ("overall", spread(&overall))])
}

fn show_batch(api: &Api, data: &Value, batch: &Value, out: &Out) -> R<Value> {
    let names = scenario_names(api, data)?;
    let mut summary = batch_summary(batch, &names, &out.lang);
    out.line(&format!("{}  {}  {} {}{}", py_none_str(&summary["batch_id"]), py_none_str(&summary["status"]), out.t("score", "分数"), fmt_score(&summary["score"]),
        if truthy(&summary["model_disabled"]) { out.t("  (no model)", "  （无模型）") } else { "" }));
    let rows: Vec<Value> = arr(&summary["runs"]).iter().map(|r| {
        let mut t = with(r, "score_text", Value::String(fmt_score(&r["score"])));
        t.as_object_mut().unwrap().insert("result".into(), Value::String(if truthy(&r["has_result"]) { out.t("yes", "有").into() } else { String::new() }));
        t
    }).collect();
    out.table(&rows, &[(out.t("Card", "任务卡"), "label"), (out.t("Status", "状态"), "status"), (out.t("Score", "分数"), "score_text"),
        (out.t("Run ID", "运行 ID"), "run_id"), (out.t("Result", "结果"), "result")]);
    if truthy(&summary["repeat_group"]) {
        let sc = repeat_summary(data, &summary["repeat_group"]);
        summary.as_object_mut().unwrap().insert("self_check".into(), sc.clone());
        if truthy(&sc) && truthy(&sc["overall"]) {
            let o = &sc["overall"];
            out.line(&format!("{}", out.t("Self-check ({0}/{1} scored): mean {2} ({3}–{4})", "自检（已完成 {0}/{1} 次）：平均 {2}（{3}–{4}）")
                .replace("{0}", &py_str(&sc["scored"])).replace("{1}", &py_str(&sc["runs"])).replace("{2}", &fmt_score(&o["mean"]))
                .replace("{3}", &fmt_score(&o["min"])).replace("{4}", &fmt_score(&o["max"]))));
        }
    }
    Ok(summary)
}

fn cmd_eval_show(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let batch = find_batch(&data, &a.str("batch").unwrap_or_default())?;
    show_batch(api, &data, &batch, out)
}

fn cmd_eval_wait(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let deadline = now() + a.int("timeout").unwrap_or(3600) as f64;
    let mut last: Option<Value> = None;
    let mut batch_id: Option<String> = None;
    loop {
        let data = polled_list(api, deadline)?;
        let target = batch_id.clone().unwrap_or_else(|| a.str("batch").unwrap_or_else(|| "latest".into()));
        let batch = find_batch(&data, &target)?;
        batch_id = Some(s(&batch["id"]));
        let runs = arr(&batch["observer_runs"]);
        let state = json!([g(&batch, "status"), runs.iter().map(|r| json!([g(r, "status"), g(r, "score")])).collect::<Vec<_>>()]);
        if last.as_ref() != Some(&state) {
            let done = runs.iter().filter(|r| FINISHED_RUN.contains(&s_or(&r["status"]).as_str())).count();
            out.line(&format!("{}  {}  {}/{} {}", time_hms(), py_none_str(&g(&batch, "status")), done, runs.len(), out.t("cards finished", "张卡已结束")));
            last = Some(state);
        }
        let status = s_or(&batch["status"]);
        if !ACTIVE.contains(&status.as_str()) {
            let summary = show_batch(api, &data, &batch, out)?;
            if status != "scored" {
                let st = py_none_str(&g(&batch, "status"));
                return Err(CliError::exit(&format!("evaluation_{}", st),
                    out.t("The evaluation ended with status {0}. See survey26 results log RUN_ID.", "评测结束，状态为 {0}。可用 survey26 results log 运行ID 查看日志。").replace("{0}", &st),
                    EXIT_FAILED));
            }
            return Ok(summary);
        }
        if now() >= deadline {
            return Err(CliError::new("wait_timeout"));
        }
        sleep_secs(std::cmp::max(5, a.int("interval").unwrap_or(20)) as f64);
    }
}

fn cmd_results_log(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let (_, run) = find_run(&data, &a.str("run").unwrap_or_default())?;
    let mut diagnostics = json!([]);
    if !a.flag("agent_only") {
        diagnostics = match api.portal("diagnostics", false, json!({"run_id": run["id"]})) {
            Ok(v) if truthy(&v) => v,
            _ => json!([]),
        };
    }
    let output = a.str("output").filter(|o| !o.is_empty());
    let full = a.flag("full") || output.is_some();
    let view = or_empty(api.portal("agent_log", false, json!({"run_id": run["id"], "full": full}))?);
    let mut text = or_blank(&view["log"]);
    if let (Some(tail), None) = (a.int("tail").filter(|t| *t != 0), &output) {
        let lines = py_splitlines(&text);
        let n = lines.len() as i64;
        let start = if tail > 0 { (n - tail).max(0) } else { (-tail).min(n) };
        text = lines[start as usize..].join("\n");
    }
    if let Some(path) = &output {
        write_file(Path::new(path), or_blank(&view["log"]).as_bytes())?;
        out.line(&format!("{}{}", out.t("Saved ", "已保存 "), path));
    } else {
        for d in arr(&diagnostics) {
            out.line(&format!("== {} · {} · {}", py_none_str(&g(&d, "kind")), py_none_str(&g(&d, "status")), or_blank(&d["code"])));
            if truthy(&d["log"]) {
                out.line(&py_str(&d["log"]));
            }
        }
        if truthy(&view["available"]) {
            out.line(&format!("== agent.log{}", if truthy(&view["truncated"]) { out.t(" (last part; --full for all)", "（末尾部分；--full 查看全部）") } else { "" }));
            out.line(&text);
        } else {
            out.line(out.t("agent.log is not available for this run; download the result ZIP instead.", "这次运行没有可读取的 agent.log，可下载结果 ZIP 查看。"));
        }
    }
    let mut result = obj(vec![("run_id", run["id"].clone()), ("diagnostics", diagnostics), ("available", Value::Bool(truthy(&view["available"]))),
        ("bytes", g(&view, "bytes")), ("truncated", Value::Bool(truthy(&view["truncated"]) && !full))]);
    let o = result.as_object_mut().unwrap();
    match output {
        Some(p) => o.insert("path".into(), Value::String(p)),
        None => o.insert("log".into(), Value::String(text)),
    };
    Ok(result)
}

fn download_run(api: &Api, run_id: &Value) -> R<Vec<u8>> {
    let once = || -> R<Vec<u8>> {
        let url = py_str(&g(&or_empty(api.portal("download_result", false, json!({"run_id": run_id}))?), "url"));
        if url.is_empty() || !(url.starts_with("https://") || url.starts_with("http://127.0.0.1")) {
            return Err(CliError::detail("download_failed", "invalid download address"));
        }
        http_get(&url)
    };
    match once() {
        Ok(v) => Ok(v),
        Err(e) => {
            if NOT_FOUND_CODES.contains(&e.code.as_str()) {
                return Err(e);
            }
            once() // one more try with a fresh signed address, like the website
        }
    }
}

fn cmd_results_download(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let (batch, run) = find_run(&data, &a.str("run").unwrap_or_default())?;
    let names = if batch.is_some() { scenario_names(api, &data)? } else { Map::new() };
    let slug = names.get(&s_or(&run["scenario_id"])).map(|n| s_or(&n["slug"])).unwrap_or_default();
    let content = download_run(api, &run["id"])?;
    let target = a.str("output").filter(|o| !o.is_empty())
        .unwrap_or_else(|| format!("result-{}-{}.zip", card_folder_name(&slug, "card"), s(&run["id"]).chars().take(8).collect::<String>()));
    write_file(Path::new(&target), &content)?;
    out.line(&format!("{}{}", out.t("Saved ", "已保存 "), target));
    Ok(obj(vec![("run_id", run["id"].clone()), ("card", if slug.is_empty() { Value::Null } else { Value::String(slug) }),
        ("path", Value::String(target)), ("bytes", json!(content.len()))]))
}

fn read_zip(bytes: Vec<u8>) -> Option<Vec<(String, Vec<u8>)>> {
    let mut z = zip::ZipArchive::new(std::io::Cursor::new(bytes)).ok()?;
    let mut entries = Vec::new();
    for i in 0..z.len() {
        let mut f = z.by_index(i).ok()?;
        let name = f.name().to_string();
        if name.ends_with('/') {
            continue;
        }
        let mut buf = Vec::new();
        f.read_to_end(&mut buf).ok()?;
        entries.push((name, buf));
    }
    Some(entries)
}

/// evaluation.json in a combined download (same fields as the website's).
fn evaluation_metadata(batch: &Value, version: Value) -> Value {
    let off = truthy(&batch["model_disabled"]);
    let mut meta = obj(vec![("evaluation_id", g(batch, "id")), ("created_at", g(batch, "created_at")), ("phase_id", g(batch, "phase_id")),
        ("revision_id", g(batch, "revision_id")), ("version", version), ("model_provided", Value::Bool(!off)), ("model_disabled", Value::Bool(off))]);
    if truthy(&batch["repeat_group"]) {
        meta = with(&meta, "self_check_group", batch["repeat_group"].clone());
    }
    meta
}

fn cmd_results_download_all(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let batch = find_batch(&data, &a.str("batch").unwrap_or_else(|| "latest".into()))?;
    let names = scenario_names(api, &data)?;
    let slug_of = |r: &Value| names.get(&s_or(&r["scenario_id"])).map(|n| s_or(&n["slug"])).unwrap_or_default();
    let mut runs: Vec<Value> = arr(&batch["observer_runs"]).into_iter().filter(|r| truthy(&r["result_path"])).collect();
    runs.sort_by(|x, y| scenario_order(&slug_of(x)).partial_cmp(&scenario_order(&slug_of(y))).unwrap());
    if runs.is_empty() {
        return Err(CliError::new("result_not_ready"));
    }
    let mut files: Vec<(String, Vec<u8>)> = Vec::new();
    let mut errors: Vec<String> = Vec::new();
    let mut cards: Vec<Value> = Vec::new();
    for (index, run) in runs.iter().enumerate() {
        let folder = ordered_card_folder(index, runs.len(), &card_folder_name(&slug_of(run), &s(&run["id"])));
        let got = download_run(api, &run["id"]).map_err(|e| e.code).and_then(|b| read_zip(b).ok_or_else(|| "bad_zip".to_string()));
        match got {
            Ok(entries) => {
                for (name, content) in flatten_result_entries(entries) {
                    let key = format!("{}/{}", folder, name);
                    match files.iter().position(|(n, _)| *n == key) {
                        Some(i) => files[i].1 = content,
                        None => files.push((key, content)),
                    }
                }
                cards.push(obj(vec![("run_id", run["id"].clone()), ("folder", Value::String(folder.clone())), ("ok", Value::Bool(true))]));
            }
            Err(code) => {
                errors.push(format!("{}: {}", folder, code));
                cards.push(obj(vec![("run_id", run["id"].clone()), ("folder", Value::String(folder.clone())), ("ok", Value::Bool(false))]));
            }
        }
        out.line(&out.t("Downloaded {0}/{1}", "已下载 {0}/{1}").replace("{0}", &(index + 1).to_string()).replace("{1}", &runs.len().to_string()));
    }
    if files.is_empty() {
        return Err(CliError::detail("download_failed", out.t("Could not download any card’s result.", "所有卡片的结果都下载失败。")));
    }
    if !errors.is_empty() {
        files.push(("errors.txt".into(), format!("{}\n", errors.join("\n")).into_bytes()));
    }
    let titles: Map<String, Value> = all_revisions(&data).iter().map(|r| (s(&r["id"]), g(r, "title"))).collect();
    let version = titles.get(&s_or(&batch["revision_id"])).cloned().unwrap_or(Value::Null);
    let meta = evaluation_metadata(&batch, version);
    files.push(("evaluation.json".into(), format!("{}\n", serde_json::to_string_pretty(&meta).unwrap()).into_bytes()));
    let suffix = if truthy(&batch["model_disabled"]) { "-no-model" } else { "" };
    let target = a.str("output").filter(|o| !o.is_empty()).unwrap_or_else(|| format!("results-{}{}.zip", s(&batch["id"]).chars().take(8).collect::<String>(), suffix));
    let write = || -> zip::result::ZipResult<()> {
        let file = std::fs::File::create(&target)?;
        let mut z = zip::ZipWriter::new(file);
        let options = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
        for (name, content) in &files {
            z.start_file(name.as_str(), options)?;
            z.write_all(content)?;
        }
        z.finish()?;
        Ok(())
    };
    write().map_err(|e| CliError::detail("write_failed", e.to_string()))?;
    out.line(&format!("{}{}{}", out.t("Saved ", "已保存 "), target,
        if errors.is_empty() { "" } else { out.t(" (some cards failed; see errors.txt)", "（部分卡片失败，详见 errors.txt）") }));
    Ok(obj(vec![("batch_id", batch["id"].clone()), ("path", Value::String(target)), ("cards", Value::Array(cards)),
        ("errors", Value::Array(errors.into_iter().map(Value::String).collect()))]))
}

// ---------------------------------------------------------------------------------------------
// Commands: final version, quota, competition, leaderboard, Kimi plan, credits

fn final_for(data: &Value) -> Option<Value> {
    arr(&data["final_versions"]).first().cloned()
}

fn cmd_final_show(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let Some(fin) = final_for(&data) else {
        out.line(&message_for("no_team_version", &out.lang, ""));
        return Ok(Value::Null);
    };
    let titles: Map<String, Value> = all_revisions(&data).iter().map(|r| (s(&r["id"]), g(r, "title"))).collect();
    let rid = if truthy(&fin["revision_id"]) { py_str(&fin["revision_id"]) } else { "-".into() };
    let title = titles.get(&s_or(&fin["revision_id"])).filter(|t| truthy(t)).map(py_str).unwrap_or_default();
    let source = match s_or(&fin["source"]).as_str() {
        "chosen" => out.t("chosen by your team", "本队已选择"),
        "best" => out.t("default: best evaluation", "默认：最高分评测"),
        _ => "",
    };
    out.line(&format!("{}{} ({}) {}", out.t("Final version: ", "最终版本："), rid, title, source));
    out.line(&format!("{}{}{}", out.t("Changeable until: ", "可修改至："), py_none_str(&g(&fin, "deadline")),
        if truthy(&fin["locked"]) { out.t(" (locked)", "（已锁定）") } else { "" }));
    Ok(fin)
}

fn final_result(result: Value) -> Value {
    let fv = g(&or_empty(result.clone()), "final_version");
    if truthy(&fv) { fv } else { result }
}

fn cmd_final_set(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let fin = final_for(&data).ok_or_else(|| CliError::new("no_team_version"))?;
    let r = find_revision(&data, &a.str("revision").unwrap_or_default())?;
    let result = api.portal("set_final_version", true, json!({"phase_id": fin["phase_id"], "revision_id": r["id"]}))?;
    out.line(out.t("Final version saved.", "已保存最终版本。"));
    Ok(final_result(result))
}

fn cmd_final_clear(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let fin = final_for(&data).ok_or_else(|| CliError::new("no_team_version"))?;
    confirm(a, out, "Clear your choice? The version of your best evaluation will be used instead.", "取消选择？将改用本队最高分评测的版本。")?;
    let result = api.portal("set_final_version", true, json!({"phase_id": fin["phase_id"], "revision_id": null}))?;
    out.line(out.t("Choice cleared; the default applies.", "已取消选择，恢复默认。"));
    Ok(final_result(result))
}

fn cmd_quota(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let data = portal_list(api)?;
    let phases: Vec<(Value, Value)> = arr(&data["phases"]).iter().map(|p| (p["phase_id"].clone(), g(&or_empty(g(p, "phases")), "slug"))).collect();
    let rows: Vec<Value> = arr(&data["quota"]).iter().map(|q| {
        let slug = phases.iter().rev().find(|(id, _)| *id == g(q, "phase_id")).map(|(_, s)| s.clone()).unwrap_or(Value::Null);
        with(q, "phase", slug)
    }).collect();
    out.table(&rows, &[(out.t("Phase", "赛程"), "phase"), (out.t("Per day", "每天"), "daily_batches"), (out.t("Used", "已用"), "used"),
        (out.t("Left", "剩余"), "remaining"), (out.t("Preparations/day", "每天可准备"), "preparations_daily"),
        (out.t("Preparations left", "剩余准备"), "preparations_remaining"), (out.t("Resets", "重置时间"), "resets_at")]);
    Ok(Value::Array(rows))
}

fn cmd_competition(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let comp = or_empty(api.rpc("current_competition", false, json!({}))?);
    let phases = arr(&api.call("phases", false, json!({}))?);
    let by_id = |id: &Value| phases.iter().rev().find(|p| &p["id"] == id).cloned().unwrap_or(Value::Null);
    let listed: Vec<Value> = phases.iter().filter(|p| ["practice-projects", "practice", "online", "final-hidden"].contains(&s_or(&p["slug"]).as_str())).cloned().collect();
    let view = obj(vec![("mode", g(&comp, "mode")), ("phase", by_id(&g(&comp, "phase_id"))), ("project_phase", by_id(&g(&comp, "project_phase_id"))),
        ("phases", Value::Array(listed.clone()))]);
    out.line(&format!("{}{}", out.t("Mode: ", "模式："), py_none_str(&view["mode"])));
    out.table(&listed, &[("slug", "slug"), (out.t("Starts", "开始"), "starts_at"), (out.t("Ends", "结束"), "ends_at"), (out.t("Active", "启用"), "is_active")]);
    Ok(view)
}

fn cmd_leaderboard(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let phases = arr(&api.call("phases", false, json!({}))?);
    let me = if a.flag("mine") { whoami(api)? } else { json!({}) };
    let team_id = g(&or_empty(g(&me, "team")), "id");
    let slug = match a.str("phase").filter(|p| !p.is_empty()) {
        Some(p) => p,
        None => {
            let comp = or_empty(api.rpc("current_competition", false, json!({}))?);
            if s_or(&comp["mode"]) == "competition" { "online".into() } else { "practice-projects".into() }
        }
    };
    let phase = phases.iter().find(|p| s_or(&p["slug"]) == slug).cloned()
        .ok_or_else(|| CliError::exit("not_found", format!("no such leaderboard: {}", slug), EXIT_NOT_FOUND))?;
    let mut settings = or_empty(g(&phase, "observer_settings"));
    if let Value::Array(list) = &settings {
        settings = list.first().cloned().unwrap_or(json!({}));
    }
    let card_arg = a.str("card").map(Value::String).unwrap_or(Value::Null);
    let (rows, card, cards);
    if truthy(&settings["projects_enabled"]) {
        let board = or_empty(api.rpc("observer_card_board", false, json!({"p_phase": phase["id"], "p_scenario_slug": card_arg, "p_limit": a.int("limit")}))?);
        if board.is_object() {
            rows = g(&board, "rows");
            card = g(&board, "scenario");
            cards = g(&board, "cards");
        } else {
            rows = board;
            card = Value::Null;
            cards = Value::Null;
        }
    } else {
        rows = api.rpc("leaderboard", false, json!({"p_phase_slug": slug, "p_limit": a.int("limit"), "p_scenario_slug": card_arg}))?;
        card = Value::Null;
        cards = Value::Null;
    }
    let rows = arr(&rows);
    let mine: Vec<Value> = rows.iter().filter(|r| truthy(&team_id) && g(r, "team_id") == team_id).cloned().collect();
    let shown = if a.flag("mine") { mine.clone() } else { rows.clone() };
    let table: Vec<Value> = shown.iter().map(|r| with(r, "score_text", Value::String(fmt_score(&r["total_score"])))).collect();
    out.table(&table, &[("#", "rank"), (out.t("Team", "队伍"), "team_name"), (out.t("Score", "分数"), "score_text"), (out.t("Evaluations", "评测次数"), "submission_count")]);
    if a.flag("mine") && mine.is_empty() {
        out.line(out.t("Your team is not on this board yet.", "本队尚未出现在这个排行榜上。"));
    }
    Ok(obj(vec![("phase", Value::String(slug)), ("card", card), ("cards", cards), ("rows", Value::Array(shown)), ("my_team_id", team_id)]))
}

fn cmd_kimi_status(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let status = or_empty(api.rpc("kimi_plan_status", false, json!({}))?);
    for key in ["has_team", "is_captain", "qualified", "eligible", "imported", "available", "code", "claimed_at"] {
        out.line(&format!("{:<12} {}", key, py_none_str(&g(&status, key))));
    }
    Ok(status)
}

fn cmd_kimi_claim(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let result = or_empty(api.rpc("claim_kimi_plan_code", true, json!({}))?);
    let already = truthy(&result["already"]);
    out.line(if already { out.t("Your team already claimed its Kimi Coding Plan code.", "你的队伍已领取过 Kimi Coding Plan 兑换码。") }
        else { out.t("Kimi Coding Plan code claimed.", "已领取 Kimi Coding Plan 兑换码。") });
    let status = or_empty(api.rpc("kimi_plan_status", false, json!({}))?);
    out.line(&or_blank(&status["code"]));
    Ok(status)
}

fn cmd_credits_list(api: &mut Api, _a: &Args, out: &Out) -> R<Value> {
    let providers = arr(&api.rpc("redeem_providers", false, json!({}))?);
    let codes = arr(&api.rpc("my_redeem_codes", false, json!({}))?);
    out.table(&providers, &[(out.t("Provider", "提供方"), "provider"), (out.t("Available", "剩余"), "available"),
        (out.t("Claimed by your team", "本队已领取"), "claimed_by_my_team")]);
    for c in &codes {
        out.line(&format!("{}: {} {}", py_none_str(&g(c, "provider")), py_none_str(&g(c, "code")), or_blank(&c["note"])));
    }
    Ok(obj(vec![("providers", Value::Array(providers)), ("codes", Value::Array(codes))]))
}

fn cmd_credits_claim(api: &mut Api, a: &Args, out: &Out) -> R<Value> {
    let result = or_empty(api.rpc("claim_redeem_code", true, json!({"p_provider": a.str("provider")}))?);
    out.line(&format!("{}: {} {}", py_none_str(&g(&result, "provider")), py_none_str(&g(&result, "code")), or_blank(&result["note"])));
    Ok(result)
}

type Command = fn(&mut Api, &Args, &Out) -> R<Value>;

fn command_for(func: &str) -> Option<Command> {
    Some(match func {
        "cmd_login" => cmd_login,
        "cmd_logout" => cmd_logout,
        "cmd_whoami" => cmd_whoami,
        "cmd_profile_show" => cmd_profile_show,
        "cmd_profile_set" => cmd_profile_set,
        "cmd_avatar_set" => cmd_avatar_set,
        "cmd_avatar_clear" => cmd_avatar_clear,
        "cmd_teammates_list" => cmd_teammates_list,
        "cmd_teammates_contact" => cmd_teammates_contact,
        "cmd_teammates_invite" => cmd_teammates_invite,
        "cmd_teammates_visibility" => cmd_teammates_visibility,
        "cmd_team_show" => cmd_team_show,
        "cmd_team_members" => cmd_team_members,
        "cmd_team_create" => cmd_team_create,
        "cmd_team_join" => cmd_team_join,
        "cmd_team_leave" => cmd_team_leave,
        "cmd_team_set" => cmd_team_set,
        "cmd_team_code" => cmd_team_code,
        "cmd_team_transfer" => cmd_team_transfer,
        "cmd_team_kick" => cmd_team_kick,
        "cmd_team_disband" => cmd_team_disband,
        "cmd_team_directory" => cmd_team_directory,
        "cmd_team_request" => cmd_team_request,
        "cmd_team_capacity" => cmd_team_capacity,
        "cmd_invites_list" => cmd_invites_list,
        "cmd_invites_respond" => cmd_invites_respond,
        "cmd_invites_cancel" => cmd_invites_cancel,
        "cmd_team_invite_uid" => cmd_team_invite_uid,
        "cmd_friends_list" => cmd_friends_list,
        "cmd_friends_add" => cmd_friends_add,
        "cmd_friends_respond" => cmd_friends_respond,
        "cmd_friends_cancel" => cmd_friends_cancel,
        "cmd_friends_remove" => cmd_friends_remove,
        "cmd_friends_block" => cmd_friends_block,
        "cmd_env_show" => cmd_env_show,
        "cmd_env_set" => cmd_env_set,
        "cmd_env_unset" => cmd_env_unset,
        "cmd_env_disable" => cmd_env_disable,
        "cmd_env_enable" => cmd_env_enable,
        "cmd_env_tag" => cmd_env_tag,
        "cmd_env_model" => cmd_env_model,
        "cmd_env_domains" => cmd_env_domains,
        "cmd_env_route" => cmd_env_route,
        "cmd_project_list" => cmd_project_list,
        "cmd_project_submit_repo" => cmd_project_submit_repo,
        "cmd_project_upload" => cmd_project_upload,
        "cmd_project_show" => cmd_project_show,
        "cmd_project_wait" => cmd_project_wait,
        "cmd_project_logs" => cmd_project_logs,
        "cmd_project_confirm" => cmd_project_confirm,
        "cmd_project_withdraw" => cmd_project_withdraw,
        "cmd_project_download" => cmd_project_download,
        "cmd_project_evidence" => cmd_project_evidence,
        "cmd_eval_start" => cmd_eval_start,
        "cmd_eval_selfcheck" => cmd_eval_selfcheck,
        "cmd_eval_list" => cmd_eval_list,
        "cmd_eval_show" => cmd_eval_show,
        "cmd_eval_wait" => cmd_eval_wait,
        "cmd_results_log" => cmd_results_log,
        "cmd_results_download" => cmd_results_download,
        "cmd_results_download_all" => cmd_results_download_all,
        "cmd_final_show" => cmd_final_show,
        "cmd_final_set" => cmd_final_set,
        "cmd_final_clear" => cmd_final_clear,
        "cmd_quota" => cmd_quota,
        "cmd_competition" => cmd_competition,
        "cmd_leaderboard" => cmd_leaderboard,
        "cmd_kimi_status" => cmd_kimi_status,
        "cmd_kimi_claim" => cmd_kimi_claim,
        "cmd_credits_list" => cmd_credits_list,
        "cmd_credits_claim" => cmd_credits_claim,
        _ => return None,
    })
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(run(argv));
}

fn run(argv: Vec<String>) -> i32 {
    let spec: Value = serde_json::from_str(SPEC).expect("spec.json");
    let a = match args::parse(&spec, &argv) {
        Ok(a) => a,
        Err(args::Exit::Code(c)) => return c,
    };
    let lang = a.lang.clone().unwrap_or_else(language);
    let command = a.command.join(" ");
    let out = Out { json: a.json, lang: lang.clone() };
    let func = a.func.clone().and_then(|f| command_for(&f));
    let Some(func) = func else {
        if a.json {
            println!("{}", py_dumps(&obj(vec![("ok", Value::Bool(false)), ("command", Value::String(command)),
                ("error", obj(vec![("code", json!("usage")), ("message", json!("missing command")), ("exit_code", json!(EXIT_USAGE))]))])));
            return EXIT_USAGE;
        }
        args::print_help(&spec, &a.command);
        return EXIT_OK;
    };
    let token = a.token.clone().filter(|t| !t.is_empty())
        .or_else(|| std::env::var("SURVEY26_TOKEN").ok().filter(|t| !t.is_empty()))
        .or_else(|| read_config().get("token").and_then(|t| t.as_str()).map(String::from).filter(|t| !t.is_empty()));
    let url = a.api.clone().filter(|u| !u.is_empty())
        .or_else(|| std::env::var("SURVEY26_API").ok().filter(|u| !u.is_empty())).unwrap_or_else(|| DEFAULT_API.into());
    let mut api = Api { token: token.map(|t| py_strip(&t)), url, retries: 3, agent: agent(60) };
    match func(&mut api, &a, &out) {
        Ok(data) => {
            if a.json {
                println!("{}", py_dumps(&obj(vec![("ok", Value::Bool(true)), ("command", Value::String(command)), ("data", data)])));
            }
            EXIT_OK
        }
        Err(e) => {
            let mut msg = message_for(&e.code, &lang, &e.detail);
            if e.code == "confirmation_required" && !e.detail.is_empty() {
                msg = format!("{} {}", e.detail, msg);
            }
            if a.json {
                println!("{}", py_dumps(&obj(vec![("ok", Value::Bool(false)), ("command", Value::String(command)),
                    ("error", obj(vec![("code", Value::String(e.code.clone())), ("message", Value::String(msg)), ("exit_code", json!(e.exit_code))]))])));
            } else {
                eprintln!("survey26: {}", msg);
            }
            e.exit_code
        }
    }
}
