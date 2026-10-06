"""Trusted executor completion and private diagnostics regressions."""
import json
import os
from pathlib import Path
import pytest
RUN="00000000-0000-4000-8000-000000000002"


def _finished_session(monkeypatch, status_payload):
    import project_platform.executor as executor
    from project_platform.session import SessionError
    events=[]
    class Transport:
        def publish_initial(self,publication):events.append('initial')
        def finish(self,reason,sequence):events.append(('finish',reason,sequence))
    class Runtime:
        def __init__(self):self.transport=Transport()
        def build(self):pass
        def start(self,environment):return self.transport
        def close(self):events.append('close')
    class Client:
        polls=0
        def call(self,action,**kwargs):
            if action=='poll':
                self.polls+=1
                if self.polls==1:return {'publication':{'schema_version':'initial-publication-v2'}}
                raise SessionError('invalid_or_expired_capability')
            if action=='ready':return {}
            if action=='status':return status_payload
            raise AssertionError(action)
    return executor, events, Runtime(), Client()


def test_execute_sends_one_finish_message_after_a_normally_scored_session(monkeypatch):
    executor,events,runtime,client=_finished_session(monkeypatch,
        {'status':'scored','expired':False,'termination_reason':'survey_complete'})
    assert executor.execute(runtime,client,{})['status']=='scored'
    assert events==['initial',('finish','survey_complete',0),'close','close']


def test_execute_without_server_side_reason_keeps_the_old_immediate_close(monkeypatch):
    # A server that predates termination_reason in the status payload: no
    # finish message, the run result is unaffected.
    executor,events,runtime,client=_finished_session(monkeypatch,{'status':'scored','expired':False})
    assert executor.execute(runtime,client,{})['status']=='scored'
    assert events==['initial','close','close']


def test_colocated_run_session_sends_finish_once_the_score_is_final(tmp_path):
    import sys
    from datetime import datetime,timedelta,timezone
    from project_platform.trusted_engine import ColocatedProvider,run_session
    from project_platform.transport import JsonlTransport
    scenario=Path(__file__).resolve().parents[1]/'archive'/'starter_kit_v3'/'scenarios'/'demo-week'
    script=tmp_path/'agent.py'
    script.write_text("""import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if m['message_type']=='initialize':continue
 if m['message_type']=='finish':
  sys.stderr.write('FINISH-MSG '+json.dumps(m['payload'],sort_keys=True)+'\\n');sys.stderr.flush();sys.exit(0)
 print(json.dumps({'protocol_version':m['protocol_version'],'message_type':'decision_response',
 'decision_sequence':m['decision_sequence'],'action':'wait'}),flush=True)
""")
    class Client:
        def __init__(self):self.actions=[]
        def call(self,action,**kwargs):
            self.actions.append(action)
            if action=='begin':
                return (datetime.now(timezone.utc)+timedelta(seconds=300)).isoformat().replace('+00:00','Z')
            return {}
    engine,participant=Client(),Client()
    transport=JsonlTransport([sys.executable,'-u',str(script)],cwd=tmp_path,environment={'PATH':os.environ['PATH']})
    provider=ColocatedProvider(transport,engine,participant)
    try:
        result,digest=run_session(scenario,tmp_path/'out',engine,provider=provider)
    finally:
        transport.close(force=True)
    assert result['termination_reason']=='survey_complete'
    markers=[line for line in transport.log.splitlines() if line.startswith('FINISH-MSG ')]
    assert len(markers)==1
    payload=json.loads(markers[0][len('FINISH-MSG '):])
    assert payload['termination_reason']=='survey_complete'
    assert payload['last_decision_sequence']==result['commit_log'][-1]['sequence']
    assert payload['grace_seconds']==30
    assert (tmp_path/'out'/'decisions.csv').is_file() and len(digest)==64


def test_colocated_run_session_sends_no_finish_after_an_agent_error(tmp_path):
    import sys
    from datetime import datetime,timedelta,timezone
    from project_platform.trusted_engine import ColocatedProvider,run_session
    from project_platform.transport import JsonlTransport
    scenario=Path(__file__).resolve().parents[1]/'archive'/'starter_kit_v3'/'scenarios'/'demo-week'
    script=tmp_path/'agent.py'
    script.write_text("""import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if m['message_type']=='initialize':continue
 print('not a protocol answer',flush=True)
""")
    class Client:
        def call(self,action,**kwargs):
            if action=='begin':
                return (datetime.now(timezone.utc)+timedelta(seconds=300)).isoformat().replace('+00:00','Z')
            return {}
    transport=JsonlTransport([sys.executable,'-u',str(script)],cwd=tmp_path,environment={'PATH':os.environ['PATH']})
    provider=ColocatedProvider(transport,Client(),Client())
    try:
        result,_=run_session(scenario,tmp_path/'out',Client(),provider=provider)
        assert result['termination_reason']=='agent_error'
        # No finish message and no stdin EOF: the process is still running
        # until the runtime's normal close() stops it.
        assert transport.process is not None and transport.process.poll() is None
    finally:
        transport.close(force=True)
    assert 'FINISH-MSG' not in transport.log


def test_expired_participant_waits_for_trusted_result_and_cannot_decide_again(monkeypatch):
    import project_platform.executor as executor
    from challenge.challenge_workflow import GlobalDeadlineExpired
    calls=[]
    class Runtime:
        def close(self):calls.append('close')
    class Client:
        states=iter(('running','scored'))
        def call(self,action,**kwargs):
            assert action=='status'
            calls.append(action)
            return {'status':next(self.states)}
    def expire(*args,**kwargs):raise GlobalDeadlineExpired()
    monkeypatch.setattr(executor,'_execute',expire)
    monkeypatch.setattr(executor.time,'sleep',lambda _:None)
    assert executor.execute(Runtime(),Client(),{})=={'status':'scored'}
    assert calls==['close','status','status','close']


def test_private_diagnostics_bound_output_and_remove_capabilities():
    from project_platform.diagnostics import private_log,safe_code
    token='obs_'+RUN+'.'+'x'*43
    value=private_log('x'*40000+'\ncompiler: error\n'+token+' https://private.test/?token=private Bearer secret '+ 'ghs_'+'a'*40,(token,))
    assert len(value.encode())<=32768
    assert 'compiler: error' in value
    assert 'private.test' not in value and token not in value and 'Bearer secret' not in value and 'ghs_' not in value
    assert safe_code(ValueError('hidden data'))=='project_operation_failed'


def test_runtime_identity_matches_private_workspace_owner_without_extra_capabilities(tmp_path):
    from project_platform.docker_runtime import DockerWorkspace
    from project_platform.manifest import ProjectManifest
    manifest=ProjectManifest.parse({'schema_version':'observer-project-v1','image':'python@sha256:'+'a'*64,'run':['python3','agent.py']})
    root=tmp_path/'project';root.mkdir(mode=0o700)
    runtime=DockerWorkspace(root,manifest,manifest.image)
    argv=runtime._command(name='test',environment={})
    assert argv[argv.index('--user')+1]==f'{root.stat().st_uid}:{root.stat().st_gid}'
    assert '--cap-drop=ALL' in argv and '--security-opt=no-new-privileges' in argv


@pytest.mark.skipif(not os.environ.get('OBSERVER_TEST_PYTHON_IMAGE'),reason='Explicit container image required')
def test_container_build_can_write_private_workspace_with_capabilities_dropped(tmp_path):
    from project_platform.docker_runtime import DockerWorkspace
    from project_platform.manifest import ProjectManifest
    root=tmp_path/'private-project';root.mkdir(mode=0o700)
    manifest=ProjectManifest.parse({'schema_version':'observer-project-v1','image':os.environ['OBSERVER_TEST_PYTHON_IMAGE'],
      'build':[['python3','-c',"from pathlib import Path;Path('built.txt').write_text('compiled')"]],
      'run':['python3','agent.py']})
    with DockerWorkspace(root,manifest,manifest.image) as runtime:
        runtime.build()
    assert (root/'built.txt').read_text()=='compiled'


def test_agent_log_is_the_scrubbed_bounded_participant_output():
    from project_platform.diagnostics import AGENT_LOG_LIMIT, agent_log
    token='obs_'+RUN+'.'+'x'*43
    stderr=('Traceback (most recent call last):\nValueError: bad tile\n'+token+'\n'
            'calling https://platform.test/functions/v1/observer-model/v1?key=abc\nAuthorization: Bearer sk-live\n')
    value=agent_log('Collecting tabulate\nSuccessfully installed tabulate\n',stderr,(token,))
    assert value.startswith('[platform] project build output\nCollecting tabulate\n')
    assert '[platform] project stderr\nTraceback (most recent call last):\nValueError: bad tile\n' in value
    assert token not in value and 'platform.test' not in value and 'sk-live' not in value
    assert agent_log('',"only stderr\n")=='[platform] project stderr\nonly stderr\n'
    # A retained tail can begin inside a credential; that partial line is dropped.
    cut=token[20:]+' leaked-suffix\nlater line\n'
    value=agent_log('',cut,(token,),truncated=True)
    assert 'leaked-suffix' not in value and value.endswith('later line\n') and 'earlier output was truncated' in value
    # Far more than two megabytes keeps the newest output, within the bound.
    value=agent_log('build\n',''.join(f'line {i:07d}\n' for i in range(400000)),(token,))
    assert len(value.encode())<=AGENT_LOG_LIMIT+64 and value.endswith('line 0399999\n')
    assert 'line 0000000' not in value and value.startswith('[platform] earlier output was truncated\n')
