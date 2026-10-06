import copy
import numpy as np
import pytest
import torch

from research.global_planning.model import PlanningNet
from research.global_planning.gates import compare, require_stage


@pytest.mark.parametrize('architecture',['gru','mlp'])
def test_candidate_permutation_and_negative_value(architecture):
    torch.manual_seed(101)
    model=PlanningNet(8,6,architecture)
    x=torch.randn(8);c=torch.randn(5,6);h=model.initial()
    logits,value,memory=model(x,c,h)
    perm=torch.tensor([3,1,4,0,2])
    moved,new_value,_=model(x,c[perm],h)
    torch.testing.assert_close(moved,logits[perm])
    torch.testing.assert_close(value,new_value)
    with torch.no_grad():
        model.value[-1].weight.zero_();model.value[-1].bias.fill_(-3.)
    assert model(x,c,h)[1].item() == -3.


def test_recurrent_history_affects_prediction():
    torch.manual_seed(101);model=PlanningNet(8,6,'gru');c=torch.randn(3,6)
    _,_,first=model(torch.ones(8),c,model.initial())
    _,_,second=model(-torch.ones(8),c,model.initial())
    a=model(torch.zeros(8),c,first)[0];b=model(torch.zeros(8),c,second)[0]
    assert not torch.allclose(a,b)


def test_gate_rejects_one_bad_card_and_missing_pairs():
    reference=[dict(id=c,card=c,total=100.,completion='survey_complete') for c in 'abcd']
    candidate=copy.deepcopy(reference)
    for r in candidate:r['total']=105.
    candidate[-1]['total']=98.
    assert not compare(candidate,reference)['promotion_passed']
    with pytest.raises(ValueError):compare(candidate[:-1],reference)
    with pytest.raises(ValueError):require_stage({},2)


def test_checkpoint_resumes_same_optimizer_update_and_excludes_labels(tmp_path):
    from research.global_planning.data import build_micro,micro_entries
    from research.global_planning.rollout import episode
    from research.global_planning.features import Normalizer,encode
    from research.global_planning.training import train_episode,save_checkpoint,load_checkpoint,CONFIG
    bundle=build_micro(micro_entries()[0],tmp_path/'bundle')
    trace=episode(bundle,tmp_path/'run',models=False)
    frames=[r['frame'] for r in trace['choices']]
    norm=Normalizer.fit(frames)
    frame=copy.deepcopy(frames[0]);left=encode(frame)
    frame['teacher']=123456;frame['future_weather_truth']='PRIVATE_SENTINEL'
    frame['final_score']=999999;frame['seed']=123
    right=encode(frame)
    for a,b in zip(left,right):np.testing.assert_array_equal(a,b)
    model=PlanningNet(len(norm.state['global']['mean']),len(norm.state['candidate']['mean']))
    optimizer=torch.optim.Adam(model.parameters(),lr=CONFIG['learning_rate'])
    train_episode(model,optimizer,norm,trace,imitation=True)
    save_checkpoint(tmp_path/'resume.pt',model,optimizer,norm,phase='imitation',epoch=1,round=0,seed=101)
    train_episode(model,optimizer,norm,trace,imitation=True)
    restored,other,restored_norm,_=load_checkpoint(tmp_path/'resume.pt')
    train_episode(restored,other,restored_norm,trace,imitation=True)
    for name,value in model.state_dict().items():torch.testing.assert_close(value,restored.state_dict()[name],rtol=0,atol=0)
