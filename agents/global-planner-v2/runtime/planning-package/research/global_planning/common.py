from pathlib import Path
import json,hashlib,os
ROOT=Path(__file__).resolve().parents[4]
AGENT=ROOT
ENGINE=ROOT/'unused-in-inference'
DEFAULT=ROOT/'unused-in-inference'
def save(path,value):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
def digest(value):
 data=value.read_bytes() if isinstance(value,Path) else json.dumps(value,sort_keys=True).encode();return hashlib.sha256(data).hexdigest()
