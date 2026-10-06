import json

import pytest

from project_platform.manifest import ProjectError
from project_platform.model_adapter import MAX_PROMPT_BYTES, propose_adapter, source_context
from project_platform.package import ProjectFile

SOURCE=(ProjectFile("agent.py",b"def choose(snapshot):\n    return {'action':'wait'}\n"),)


def proposal():
    return {"manifest":{"schema_version":"observer-project-v1","image":"python:3.12-slim",
        "run":["python3","-u",".observer-adapter/main.py"]},
        "files":[{"path":".observer-adapter/main.py","content":"from agent import choose\n"}],
        "explanation":"Calls the original agent.choose function; only translates the protocol."}


def model_response(value):
    return {"choices":[{"message":{"content":json.dumps(value)}}]}


def test_model_proposal_is_bound_to_original_source_and_cannot_edit_it():
    requests=[]
    def complete(request):
        requests.append(request)
        return model_response(proposal())
    adapted=propose_adapter(SOURCE,"test-model",complete)
    assert adapted.files[0].path==".observer-adapter/main.py"
    assert SOURCE[0].data==b"def choose(snapshot):\n    return {'action':'wait'}\n"
    assert requests[0]["max_tokens"]==4096
    bad=proposal(); bad["files"][0]["path"]="agent.py"
    with pytest.raises(ProjectError,match="cannot be replaced"):
        propose_adapter(SOURCE,"test-model",lambda _:model_response(bad))


def test_unknown_interface_stops_instead_of_substituting_a_reference_agent():
    with pytest.raises(ProjectError,match="could not identify the entry point"):
        propose_adapter(SOURCE,"test",lambda _:model_response({
            "error":"manual_interface_required","explanation":"No callable agent found"}))


def test_large_complete_project_gets_explicitly_truncated_model_context():
    source=tuple(ProjectFile(f"src/module{i}.rs",b"x"*20000) for i in range(100))
    context=source_context(source)
    assert context["file_count"]==100
    assert sum(len(item["content"].encode()) for item in context["source_samples"])<=32768
    assert all(item["truncated"] for item in context["source_samples"])
    def complete(request):
        assert len(json.dumps(request,ensure_ascii=False).encode())<MAX_PROMPT_BYTES
        return model_response(proposal())
    propose_adapter(source,"test",complete)


def test_malformed_or_ambiguous_model_output_is_not_accepted():
    for text in (chr(96)*3+"json\n{}\n"+chr(96)*3,"not JSON",'{"manifest":{},"manifest":{}}'):
        with pytest.raises(ProjectError):
            propose_adapter(SOURCE,"test",lambda _:{"choices":[{"message":{"content":text}}]})



def test_a_fenced_proposal_is_unwrapped():
    text=chr(96)*3+"json\n"+json.dumps(proposal())+"\n"+chr(96)*3
    adapted=propose_adapter(SOURCE,"test",lambda _:{"choices":[{"message":{"content":text}}]})
    assert adapted.files[0].path==".observer-adapter/main.py"


def test_an_invalid_proposal_is_requested_once_more_with_the_rejected_rule():
    bad=proposal(); bad["manifest"]["build"]="pip install nothing"
    answers=[{"choices":[{"message":{"content":"Here is the adapter: {"}}]},model_response(proposal())]
    requests=[]
    def complete(request):
        requests.append(request)
        return answers[len(requests)-1]
    assert propose_adapter(SOURCE,"test",complete).files[0].path==".observer-adapter/main.py"
    assert len(requests)==2 and requests[0]["messages"]==requests[1]["messages"][:2]
    assert "did not return a valid interface proposal" in requests[1]["messages"][2]["content"]
    requests.clear()
    with pytest.raises(ProjectError,match="build"):
        propose_adapter(SOURCE,"test",lambda request:requests.append(request) or model_response(bad))
    assert len(requests)==2 and "build" in requests[1]["messages"][2]["content"]


def test_a_missing_entry_point_is_not_asked_again():
    requests=[]
    with pytest.raises(ProjectError,match="could not identify the entry point"):
        propose_adapter(SOURCE,"test",lambda request:requests.append(request) or model_response({
            "error":"manual_interface_required","explanation":"No callable agent found"}))
    assert len(requests)==1


V3_PROMPT_SHA256 = "186b2bfa3da956350af0fe679db504d6d8951255e25bbac475eb72c23705c833"


def _system_prompt(**kwargs):
    requests = []

    def complete(request):
        requests.append(request)
        return model_response(proposal())
    propose_adapter(SOURCE, "test-model", complete, **kwargs)
    return requests[0]["messages"][0]["content"]


def test_v3_adapter_prompt_is_unchanged_and_the_default():
    import hashlib
    prompt = _system_prompt()
    assert prompt == _system_prompt(gameplay="v3")
    assert hashlib.sha256(prompt.encode()).hexdigest() == V3_PROMPT_SHA256
    assert "participant-agent-protocol-v2" in prompt and "protocol-v4" not in prompt


def test_v4_adapter_prompt_describes_protocol_v4():
    prompt = _system_prompt(gameplay="v4")
    assert "participant-agent-protocol-v2" not in prompt and "tile_id" not in prompt
    for needle in ('"protocol_version":"participant-agent-protocol-v4"', "v4-initialize-v1", "v4-decision-snapshot-v1",
                   '"action":"observe","pointing"', "until_utc", '{"action":"finish"}', 'protocol:"jsonl-v4"',
                   "message_type is finish", "TOP LEVEL"):
        assert needle in prompt, needle
    v4 = proposal()
    v4["manifest"]["protocol"] = "jsonl-v4"  # what the v4 prompt asks for; accepted as the same transport
    adapted = propose_adapter(SOURCE, "test", lambda _: model_response(v4), gameplay="v4")
    assert adapted.manifest.protocol == "jsonl-v2"
    with pytest.raises(ProjectError, match="Unknown gameplay"):
        propose_adapter(SOURCE, "test", lambda _: model_response(proposal()), gameplay="v5")


def test_large_project_still_fits_with_the_v4_prompt():
    source = tuple(ProjectFile(f"src/module{i}.rs", b"x" * 20000) for i in range(100))

    def complete(request):
        assert len(json.dumps(request, ensure_ascii=False).encode()) < MAX_PROMPT_BYTES
        return model_response(proposal())
    propose_adapter(source, "test", complete, gameplay="v4")


def test_a_project_without_code_is_refused_without_a_model_call_and_names_its_files():
    from project_platform.package import ProjectFile
    calls=[]
    files=(ProjectFile("agent/.env.example",b"KEY=1\n"),ProjectFile(".gitignore",b"*.pyc\n"),ProjectFile("README.md",b"x"))
    with pytest.raises(ProjectError) as error:
        propose_adapter(files,"m",lambda request:calls.append(request))
    text=str(error.value)
    assert calls==[] and "No code files" in text and "agent/.env.example" in text and "observer.project.json" in text


def test_the_entry_point_error_lists_the_files_and_points_to_the_manifest():
    with pytest.raises(ProjectError) as error:
        propose_adapter(SOURCE,"test",lambda _:model_response({"error":"manual_interface_required","explanation":"x"}))
    assert "Files seen: "+SOURCE[0].path in str(error.value) and "examples/python" in str(error.value)
