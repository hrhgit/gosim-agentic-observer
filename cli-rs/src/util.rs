//! Python-compatible helpers, so that both builds print the same JSON and text.
use serde_json::{Map, Value};

/// Python truthiness of a JSON value.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map_or(true, |f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// The string inside a JSON string (empty otherwise).
pub fn s(v: &Value) -> String {
    v.as_str().map(String::from).unwrap_or_else(|| if v.is_null() { String::new() } else { pystr(v) })
}

/// `value or ""` for strings.
pub fn s_or(v: &Value) -> String {
    if truthy(v) { s(v) } else { String::new() }
}

/// `value or []`.
pub fn arr(v: &Value) -> Vec<Value> {
    v.as_array().cloned().unwrap_or_default()
}

/// `dict.get(key)` (None when missing).
pub fn g(v: &Value, key: &str) -> Value {
    v.get(key).cloned().unwrap_or(Value::Null)
}

/// `value or {}`.
pub fn or_empty(v: Value) -> Value {
    if truthy(&v) { v } else { Value::Object(Map::new()) }
}

pub fn obj(items: Vec<(&str, Value)>) -> Value {
    let mut m = Map::new();
    for (k, v) in items {
        m.insert(k.to_string(), v);
    }
    Value::Object(m)
}

/// `dict(v, key=value)`.
pub fn with(v: &Value, key: &str, value: Value) -> Value {
    let mut m = v.as_object().cloned().unwrap_or_default();
    m.insert(key.to_string(), value);
    Value::Object(m)
}

/// Python `str(value)`.
pub fn pystr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        other => py_dumps(other),
    }
}

pub fn py_str(v: &Value) -> String {
    pystr(v)
}

pub fn py_none_str(v: &Value) -> String {
    pystr(v)
}

/// `"" if value is None else str(value)`.
pub fn blank_none(v: &Value) -> String {
    if v.is_null() { String::new() } else { pystr(v) }
}

/// `str(value or "")`.
pub fn or_blank(v: &Value) -> String {
    if truthy(v) { pystr(v) } else { String::new() }
}

/// Python `json.dumps(value, ensure_ascii=False)` (", " and ": " separators).
pub fn py_dumps(v: &Value) -> String {
    let mut out = String::new();
    write_value(v, &mut out);
    out
}

fn write_value(v: &Value, out: &mut String) {
    match v {
        Value::Array(a) => {
            out.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_value(x, out);
            }
            out.push(']');
        }
        Value::Object(o) => {
            out.push('{');
            for (i, (k, x)) in o.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                out.push_str(&serde_json::to_string(k).unwrap());
                out.push_str(": ");
                write_value(x, out);
            }
            out.push('}');
        }
        other => out.push_str(&serde_json::to_string(other).unwrap()),
    }
}

/// Python `json.dumps(value, ensure_ascii=False, indent=2)`.
pub fn py_dumps_indent(v: &Value) -> String {
    serde_json::to_string_pretty(v).unwrap()
}

fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python `str.strip()`.
pub fn py_strip(t: &str) -> String {
    t.trim_matches(py_space).to_string()
}

/// Python `str.splitlines()`.
pub fn py_splitlines(t: &str) -> Vec<String> {
    let mut lines = Vec::new();
    let mut cur = String::new();
    let mut chars = t.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '\r' => {
                if chars.peek() == Some(&'\n') {
                    chars.next();
                }
                lines.push(std::mem::take(&mut cur));
            }
            '\n' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}' => {
                lines.push(std::mem::take(&mut cur));
            }
            _ => cur.push(c),
        }
    }
    if !cur.is_empty() {
        lines.push(cur);
    }
    lines
}

/// Python `items[:limit]`.
pub fn py_slice(items: &[Value], limit: i64) -> Vec<Value> {
    let n = items.len() as i64;
    let end = if limit >= 0 { limit.min(n) } else { (n + limit).max(0) };
    items[..end as usize].to_vec()
}

pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

pub fn is_uuid(v: &str) -> bool {
    let b = v.as_bytes();
    b.len() == 36 && b.iter().enumerate().all(|(i, c)| match i {
        8 | 13 | 18 | 23 => *c == b'-',
        _ => c.is_ascii_digit() || (b'a'..=b'f').contains(c),
    })
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146097 + doe - 719468
}

/// ISO-8601 timestamp (as PostgreSQL and the gateway print it) to seconds since the epoch.
/// A value without an offset is taken as UTC.
pub fn parse_time(value: &str) -> Option<f64> {
    let t = value.trim();
    if t.len() < 10 {
        return None;
    }
    let num = |a: usize, b: usize| t.get(a..b).and_then(|x| x.parse::<i64>().ok());
    let (y, mo, d) = (num(0, 4)?, num(5, 7)?, num(8, 10)?);
    let mut secs = days_from_civil(y, mo, d) * 86400;
    let mut frac = 0.0;
    let rest = &t[10..];
    let mut tz = 0i64;
    if !rest.is_empty() {
        let rest = &rest[1..];
        let (h, mi) = (rest.get(0..2)?.parse::<i64>().ok()?, rest.get(3..5)?.parse::<i64>().ok()?);
        let mut idx = 5;
        let mut sec = 0;
        if rest.get(5..6) == Some(":") {
            sec = rest.get(6..8)?.parse::<i64>().ok()?;
            idx = 8;
        }
        secs += h * 3600 + mi * 60 + sec;
        let mut tail = &rest[idx..];
        if let Some(stripped) = tail.strip_prefix('.') {
            let digits: String = stripped.chars().take_while(|c| c.is_ascii_digit()).collect();
            frac = format!("0.{}", digits).parse::<f64>().unwrap_or(0.0);
            tail = &stripped[digits.len()..];
        }
        if tail == "Z" || tail.is_empty() {
            tz = 0;
        } else {
            let sign = if tail.starts_with('-') { -1 } else { 1 };
            let body = &tail[1..];
            let (th, tm) = if body.contains(':') {
                (body.get(0..2)?.parse::<i64>().ok()?, body.get(3..5)?.parse::<i64>().ok()?)
            } else {
                (body.get(0..2)?.parse::<i64>().ok()?, body.get(2..4).and_then(|x| x.parse::<i64>().ok()).unwrap_or(0))
            };
            tz = sign * (th * 3600 + tm * 60);
        }
    }
    Some((secs - tz) as f64 + frac)
}

pub fn base64(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b = [chunk[0], *chunk.get(1).unwrap_or(&0), *chunk.get(2).unwrap_or(&0)];
        let n = ((b[0] as u32) << 16) | ((b[1] as u32) << 8) | b[2] as u32;
        out.push(T[(n >> 18) as usize & 63] as char);
        out.push(T[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 { T[(n >> 6) as usize & 63] as char } else { '=' });
        out.push(if chunk.len() > 2 { T[n as usize & 63] as char } else { '=' });
    }
    out
}

pub fn random_hex(bytes: usize) -> String {
    use std::hash::{BuildHasher, Hasher};
    let mut out = String::new();
    while out.len() < bytes * 2 {
        let mut h = std::collections::hash_map::RandomState::new().build_hasher();
        h.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
        out.push_str(&format!("{:016x}", h.finish()));
    }
    out.truncate(bytes * 2);
    out
}
