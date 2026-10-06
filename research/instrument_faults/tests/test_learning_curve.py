import pytest

from research.instrument_faults.learning_curve import nested_cohorts, train_job


def test_balanced_nested_training_only():
    entries = [dict(id=f'{card}-{i}', card=card, split='train', fault_count=(0, 1, 1, 3)[i % 4])
               for card in 'abcd' for i in range(80)]
    entries += [dict(id='held-out', card='a', split='test', fault_count=0)]
    groups = nested_cohorts(entries, [20, 40, 80, 160, 320])
    previous = set()
    for n, group in groups.items():
        ids = {e['id'] for e in group}
        assert len(ids) == n and previous <= ids
        assert all(sum(e['card'] == c for e in group) == n//4 for c in 'abcd')
        assert {e['fault_count'] for e in group} == {0, 1, 3}
        assert all(e['split'] == 'train' for e in group)
        previous = ids
    assert groups == nested_cohorts(entries, [20, 40, 80, 160, 320])


def test_forbid_final_test_in_workers(tmp_path):
    with pytest.raises(ValueError, match='train and validation'):
        train_job(tmp_path, tmp_path, [dict(split='test')], [], 'logistic', 2)
    with pytest.raises(ValueError, match='multiples of four'):
        nested_cohorts([], [21])
