//! Command-line parsing driven by the shared cli/spec.json (the same commands, options, choices
//! and defaults as the Python build's argparse parser, which tests check against the spec).
use serde_json::Value;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub enum Parsed {
    Bool(bool),
    Str(String),
    Int(i64),
    List(Vec<String>),
}

pub struct Args {
    pub command: Vec<String>,
    pub func: Option<String>,
    pub json: bool,
    pub token: Option<String>,
    pub lang: Option<String>,
    pub api: Option<String>,
    values: HashMap<String, Parsed>,
}

pub enum Exit {
    Code(i32),
}

impl Args {
    pub fn get(&self, dest: &str) -> Option<&Parsed> {
        self.values.get(dest)
    }
    pub fn flag(&self, dest: &str) -> bool {
        matches!(self.values.get(dest), Some(Parsed::Bool(true)))
    }
    pub fn str(&self, dest: &str) -> Option<String> {
        if dest == "token" {
            return self.token.clone();
        }
        match self.values.get(dest) {
            Some(Parsed::Str(s)) => Some(s.clone()),
            Some(Parsed::Int(i)) => Some(i.to_string()),
            _ => None,
        }
    }
    pub fn int(&self, dest: &str) -> Option<i64> {
        match self.values.get(dest) {
            Some(Parsed::Int(i)) => Some(*i),
            _ => None,
        }
    }
    pub fn list(&self, dest: &str) -> Vec<String> {
        match self.values.get(dest) {
            Some(Parsed::List(l)) => l.clone(),
            _ => vec![],
        }
    }
}

const COMMON: [(&str, &str); 4] = [("--json", "json"), ("--token", "token"), ("--lang", "lang"), ("--api", "api")];

fn commands(spec: &Value) -> &Vec<Value> {
    spec["commands"].as_array().expect("spec commands")
}

fn entry<'a>(spec: &'a Value, path: &[String]) -> Option<&'a Value> {
    commands(spec).iter().find(|c| c["path"].as_array().map_or(false, |p| p.len() == path.len() && p.iter().zip(path).all(|(a, b)| a == b)))
}

fn children<'a>(spec: &'a Value, path: &[String]) -> Vec<&'a Value> {
    commands(spec).iter().filter(|c| {
        let p = c["path"].as_array().unwrap();
        p.len() == path.len() + 1 && p.iter().zip(path).all(|(a, b)| a == b)
    }).collect()
}

fn negative_number(t: &str) -> bool {
    let body = &t[1..];
    !body.is_empty() && (body.chars().all(|c| c.is_ascii_digit())
        || (body.contains('.') && body.split_once('.').map_or(false, |(a, b)| a.chars().all(|c| c.is_ascii_digit()) && !b.is_empty() && b.chars().all(|c| c.is_ascii_digit()))))
}

fn is_option(t: &str) -> bool {
    t.starts_with('-') && t != "-" && !negative_number(t)
}

fn fail(path: &[String], message: &str) -> Exit {
    let prog = if path.is_empty() { "survey26".to_string() } else { format!("survey26 {}", path.join(" ")) };
    eprintln!("usage: {} [-h] ...\n{}: error: {}", prog, prog, message);
    Exit::Code(2)
}

fn py_int(t: &str) -> Option<i64> {
    let t = t.trim().replace('_', "");
    let t = t.strip_prefix('+').unwrap_or(&t);
    t.parse::<i64>().ok()
}

fn choice_text(v: &Value) -> String {
    match v {
        Value::String(s) => format!("'{}'", s),
        other => other.to_string(),
    }
}

fn convert(path: &[String], opt: &Value, name: &str, raw: &str) -> Result<Parsed, Exit> {
    let value = if opt["type"] == "int" {
        match py_int(raw) {
            Some(i) => Parsed::Int(i),
            None => return Err(fail(path, &format!("argument {}: invalid int value: '{}'", name, raw))),
        }
    } else {
        Parsed::Str(raw.to_string())
    };
    if let Some(choices) = opt["choices"].as_array() {
        let ok = choices.iter().any(|c| match (&value, c) {
            (Parsed::Int(i), Value::Number(n)) => n.as_i64() == Some(*i),
            (Parsed::Str(s), Value::String(c)) => s == c,
            _ => false,
        });
        if !ok {
            let list: Vec<String> = choices.iter().map(choice_text).collect();
            let shown = if opt["type"] == "int" { raw.to_string() } else { format!("'{}'", raw) };
            return Err(fail(path, &format!("argument {}: invalid choice: {} (choose from {})", name, shown, list.join(", "))));
        }
    }
    Ok(value)
}

pub fn parse(spec: &Value, argv: &[String]) -> Result<Args, Exit> {
    let mut args = Args { command: vec![], func: None, json: false, token: None, lang: None, api: None, values: HashMap::new() };
    let mut current: Option<&Value> = None; // None = the top level
    let mut positionals: Vec<String> = Vec::new();
    let mut only_positional = false;
    let mut i = 0;
    while i < argv.len() {
        let tok = argv[i].clone();
        i += 1;
        if !only_positional && tok == "--" {
            only_positional = true;
            continue;
        }
        if !only_positional && is_option(&tok) {
            let (name, inline) = match tok.split_once('=') {
                Some((n, v)) if tok.starts_with("--") => (n.to_string(), Some(v.to_string())),
                _ => (tok.clone(), None),
            };
            if name == "-h" || name == "--help" {
                print_help(spec, &args.command);
                return Err(Exit::Code(0));
            }
            if name == "--version" && current.is_none() {
                println!("survey26 {}", crate::VERSION);
                return Err(Exit::Code(0));
            }
            let empty = vec![];
            let options: &Vec<Value> = current.and_then(|c| c["options"].as_array()).unwrap_or(&empty);
            // Exact flag, short option with an attached value, or a unique prefix of a long option (as argparse).
            let mut longs: Vec<String> = COMMON.iter().map(|c| c.0.to_string()).collect();
            if current.is_none() {
                longs.push("--version".into());
                longs.push("--help".into());
            }
            for o in options {
                for f in o["flags"].as_array().unwrap() {
                    longs.push(f.as_str().unwrap().to_string());
                }
            }
            let mut name = name;
            let mut inline = inline;
            if !longs.contains(&name) {
                if name.starts_with("--") {
                    let hits: Vec<&String> = longs.iter().filter(|l| l.starts_with("--") && l.starts_with(&name)).collect();
                    if hits.len() > 1 {
                        let list: Vec<String> = hits.iter().map(|s| s.to_string()).collect();
                        return Err(fail(&args.command, &format!("ambiguous option: {} could match {}", name, list.join(", "))));
                    }
                    if let Some(h) = hits.first() {
                        name = h.to_string();
                    }
                } else if name.len() > 2 && longs.contains(&name[..2].to_string()) {
                    inline = Some(name[2..].to_string());
                    name = name[..2].to_string();
                }
            }
            if name == "--version" && current.is_none() {
                println!("survey26 {}", crate::VERSION);
                return Err(Exit::Code(0));
            }
            if name == "--help" {
                print_help(spec, &args.command);
                return Err(Exit::Code(0));
            }
            if let Some((_, dest)) = COMMON.iter().find(|c| c.0 == name) {
                if *dest == "json" {
                    if inline.is_some() {
                        return Err(fail(&args.command, &format!("argument --json: ignored explicit argument '{}'", inline.unwrap())));
                    }
                    args.json = true;
                    continue;
                }
                let value = match inline {
                    Some(v) => v,
                    None => {
                        if i < argv.len() && !is_option(&argv[i]) {
                            i += 1;
                            argv[i - 1].clone()
                        } else {
                            return Err(fail(&args.command, &format!("argument {}: expected one argument", name)));
                        }
                    }
                };
                match *dest {
                    "token" => args.token = Some(value),
                    "lang" => {
                        if value != "en" && value != "zh" {
                            return Err(fail(&args.command, &format!("argument --lang: invalid choice: '{}' (choose from 'en', 'zh')", value)));
                        }
                        args.lang = Some(value)
                    }
                    _ => args.api = Some(value),
                }
                continue;
            }
            let Some(opt) = options.iter().find(|o| o["flags"].as_array().unwrap().iter().any(|f| f == name.as_str())) else {
                return Err(fail(&args.command, &format!("unrecognized arguments: {}", tok)));
            };
            let dest = opt["dest"].as_str().unwrap().to_string();
            let display = opt["flags"].as_array().unwrap().iter().map(|f| f.as_str().unwrap()).collect::<Vec<_>>().join("/");
            match opt["kind"].as_str().unwrap() {
                "flag" | "flag_false" => {
                    if let Some(v) = inline {
                        return Err(fail(&args.command, &format!("argument {}: ignored explicit argument '{}'", display, v)));
                    }
                    args.values.insert(dest, Parsed::Bool(opt["kind"] == "flag"));
                }
                _ => {
                    let raw = match inline {
                        Some(v) => v,
                        None => {
                            if i < argv.len() && !is_option(&argv[i]) {
                                i += 1;
                                argv[i - 1].clone()
                            } else {
                                return Err(fail(&args.command, &format!("argument {}: expected one argument", display)));
                            }
                        }
                    };
                    let v = convert(&args.command, opt, &display, &raw)?;
                    args.values.insert(dest, v);
                }
            }
            continue;
        }
        // A positional: a subcommand while at a group, otherwise the command's own arguments.
        let kids = children(spec, &args.command);
        if !kids.is_empty() && positionals.is_empty() {
            let Some(child) = kids.iter().find(|c| c["path"].as_array().unwrap().last().unwrap() == tok.as_str()) else {
                let list: Vec<String> = kids.iter().map(|c| format!("'{}'", c["path"].as_array().unwrap().last().unwrap().as_str().unwrap())).collect();
                return Err(fail(&args.command, &format!("argument COMMAND: invalid choice: '{}' (choose from {})", tok, list.join(", "))));
            };
            args.command.push(tok);
            current = Some(child);
            continue;
        }
        positionals.push(tok);
    }
    let Some(entry) = current.or_else(|| entry(spec, &args.command)) else {
        return Ok(args);
    };
    args.func = entry["func"].as_str().map(String::from);
    // Option defaults (a flag whose spec default is null stays unset, like argparse's default=None).
    for o in entry["options"].as_array().unwrap() {
        let dest = o["dest"].as_str().unwrap().to_string();
        if args.values.contains_key(&dest) {
            continue;
        }
        match o["kind"].as_str().unwrap() {
            "flag" => {
                if !o.get("default").map_or(false, |d| d.is_null()) {
                    args.values.insert(dest, Parsed::Bool(false));
                }
            }
            "flag_false" => {}
            _ => match o.get("default") {
                Some(Value::Number(n)) => {
                    args.values.insert(dest, Parsed::Int(n.as_i64().unwrap_or(0)));
                }
                Some(Value::String(s)) => {
                    args.values.insert(dest, Parsed::Str(s.clone()));
                }
                _ => {}
            },
        }
    }
    let specs = entry["positionals"].as_array().unwrap();
    let mut pending = positionals.into_iter();
    let mut missing: Vec<String> = Vec::new();
    let mut extra: Vec<String> = Vec::new();
    for (index, p) in specs.iter().enumerate() {
        let name = p["name"].as_str().unwrap().to_string();
        match &p["nargs"] {
            Value::String(n) if n == "+" => {
                let rest: Vec<String> = pending.by_ref().collect();
                if rest.is_empty() {
                    missing.push(name);
                } else {
                    args.values.insert(name, Parsed::List(rest));
                }
            }
            Value::String(n) if n == "?" => {
                // argparse fills optional positionals only after every required one.
                let required_after = specs[index + 1..].iter().filter(|q| q["nargs"] != "?").count();
                let left = pending.len();
                if left > required_after {
                    let raw = pending.next().unwrap();
                    let v = convert(&args.command, p, &name, &raw)?;
                    args.values.insert(name, v);
                } else if let Some(Value::String(d)) = p.get("default") {
                    args.values.insert(name, Parsed::Str(d.clone()));
                }
            }
            _ => match pending.next() {
                Some(raw) => {
                    let v = convert(&args.command, p, &name, &raw)?;
                    args.values.insert(name, v);
                }
                None => missing.push(name),
            },
        }
    }
    extra.extend(pending);
    // Required options are reported before the positionals, as argparse does.
    let mut required: Vec<String> = entry["options"].as_array().unwrap().iter()
        .filter(|o| o["required"] == true && !args.values.contains_key(o["dest"].as_str().unwrap()))
        .map(|o| o["flags"].as_array().unwrap().iter().map(|f| f.as_str().unwrap()).collect::<Vec<_>>().join("/")).collect();
    required.extend(missing);
    let missing = required;
    if !missing.is_empty() && children(spec, &args.command).is_empty() {
        return Err(fail(&args.command, &format!("the following arguments are required: {}", missing.join(", "))));
    }
    if !extra.is_empty() {
        return Err(fail(&args.command, &format!("unrecognized arguments: {}", extra.join(" "))));
    }
    Ok(args)
}

/// Help text generated from the spec.
pub fn print_help(spec: &Value, path: &[String]) {
    let prog = if path.is_empty() { "survey26".to_string() } else { format!("survey26 {}", path.join(" ")) };
    let entry = entry(spec, path);
    let kids = children(spec, path);
    let mut usage = format!("usage: {} [-h] [--json] [--token TOKEN] [--lang {{en,zh}}]", prog);
    if path.is_empty() {
        usage.push_str(" [--version]");
    }
    if let Some(e) = entry {
        for o in e["options"].as_array().unwrap() {
            let flag = o["flags"].as_array().unwrap()[0].as_str().unwrap().to_string();
            usage.push_str(&if o["kind"] == "value" { format!(" [{} {}]", flag, metavar(o)) } else { format!(" [{}]", flag) });
        }
        for p in e["positionals"].as_array().unwrap() {
            let n = p["name"].as_str().unwrap();
            usage.push_str(&match p["nargs"].as_str() { Some("?") => format!(" [{}]", n), Some("+") => format!(" {} [{} ...]", n, n), _ => format!(" {}", n) });
        }
    }
    if !kids.is_empty() {
        usage.push_str(if path.is_empty() { " COMMAND ..." } else { " ACTION ..." });
    }
    println!("{}\n", usage);
    let description = if path.is_empty() { spec["description"].as_str().unwrap_or("") } else { entry.and_then(|e| e["help"].as_str()).unwrap_or("") };
    if !description.is_empty() {
        println!("{}\n", description);
    }
    if !kids.is_empty() {
        println!("{}:", if path.is_empty() { "commands" } else { "actions" });
        for k in &kids {
            println!("  {:<14} {}", k["path"].as_array().unwrap().last().unwrap().as_str().unwrap(), k["help"].as_str().unwrap_or(""));
        }
        println!();
    }
    if let Some(e) = entry {
        let pos = e["positionals"].as_array().unwrap();
        if !pos.is_empty() {
            println!("positional arguments:");
            for p in pos {
                println!("  {:<22} {}", p["name"].as_str().unwrap(), p["help"].as_str().unwrap_or(""));
            }
            println!();
        }
    }
    println!("options:");
    println!("  {:<22} {}", "-h, --help", "show this help message and exit");
    if path.is_empty() {
        println!("  {:<22} {}", "--version", "show program's version number and exit");
    }
    println!("  {:<22} {}", "--json", "print one JSON object (stable schema) instead of text");
    println!("  {:<22} {}", "--token TOKEN", "API token (default: $SURVEY26_TOKEN, then the saved login)");
    println!("  {:<22} {}", "--lang {en,zh}", "message language (default: $SURVEY26_LANG or $LANG)");
    if let Some(e) = entry {
        for o in e["options"].as_array().unwrap() {
            let flags = o["flags"].as_array().unwrap().iter().map(|f| f.as_str().unwrap()).collect::<Vec<_>>().join(", ");
            let label = if o["kind"] == "value" { format!("{} {}", flags, metavar(o)) } else { flags };
            println!("  {:<22} {}", label, o["help"].as_str().unwrap_or(""));
        }
    }
    if path.is_empty() {
        println!("\n{}", spec["epilog"].as_str().unwrap_or(""));
    }
}

fn metavar(o: &Value) -> String {
    if let Some(m) = o["metavar"].as_str() {
        return m.to_string();
    }
    if let Some(c) = o["choices"].as_array() {
        return format!("{{{}}}", c.iter().map(|v| match v { Value::String(s) => s.clone(), other => other.to_string() }).collect::<Vec<_>>().join(","));
    }
    o["dest"].as_str().unwrap_or("VALUE").to_uppercase()
}
