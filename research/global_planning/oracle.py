"""Exhaustive *micro-only* legal candidate tree, scored by the official engine.

This is evaluator-only code. It is never imported by deployed inference or used to
provide hidden information to the policy. No search budget truncates the tree.
"""
from __future__ import annotations

from .common import digest, save
from .data import build_micro
from .rollout import episode
from .training import Policy


def exhaustive(bundle, output, *, node=None):
    stack=[()]
    leaves=[]
    visits=0
    while stack:
        prefix=stack.pop()
        position=0
        def choose(frame):
            nonlocal position
            selected=prefix[position] if position<len(prefix) else frame['teacher']
            position+=1
            return selected,{}
        trace=episode(bundle,output/digest(list(prefix))[:16],choose,node=node,models=True,
                      reuse_key=digest(dict(prefix=prefix,oracle='micro-exhaustive-v1')))
        visits+=1
        if len(prefix)<len(trace['choices']):
            count=len(trace['choices'][len(prefix)]['frame']['candidates'])
            stack.extend(prefix+(i,) for i in range(count))
        else:
            leaves.append(dict(choices=list(prefix),total=trace['total']))
    best=max(leaves,key=lambda r:r['total'])
    result=dict(best=best,leaves=len(leaves),engine_runs=visits,complete_enumeration=True,
                scope='Original candidate generator and forced non-planning actions in this micro scene')
    save(output/'oracle.json',result)
    return result


def capability(model,normalizer,output,*,node=None):
    rows=[]
    for i,kind in enumerate(('request_group','coverage','repeat')):
        entry=dict(id=f'capability-{kind}',split='capability',seed=8400000+i,kind=kind,
                   card='micro',steps=3,source='SELF-GENERATED held-out diagnostic')
        bundle=build_micro(entry,output/'scenarios'/entry['id'])
        oracle=exhaustive(bundle,output/'oracle'/entry['id'],node=node)
        trace=episode(bundle,output/'model'/entry['id'],Policy(model,normalizer),node=node,models=True,
                      reuse_key='frozen-capability-v1')
        rows.append(dict(id=entry['id'],oracle=oracle['best']['total'],model=trace['total'],
                         regret=oracle['best']['total']-trace['total'],leaves=oracle['leaves']))
    result=dict(rows=rows,passed=all(abs(r['regret'])<=1e-6 for r in rows),
                limitation='Optimum within the available candidate tree, not every continuous pointing/action')
    save(output/'summary.json',result)
    return result
