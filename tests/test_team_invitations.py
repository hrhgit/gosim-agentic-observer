"""Real membership, privacy, notifications and concurrent last-place decisions."""
import concurrent.futures
import uuid

import psycopg
import pytest

from test_project_database import database, identity, query, rpc  # noqa: F401


def newcomer(uri):
    user = uuid.uuid4()
    query(uri, 'insert into auth.users(id,email) values(%s,%s)', (user,f'{user}@example.test'))
    query(uri, 'update public.profiles set show_on_wall=true where id=%s', (user,))
    return user


@pytest.mark.parametrize('kind', ['invite','request'])
def test_requests_and_invitations_require_consent_and_notify_both_parties(database,kind):
    uri = database
    leader,team = identity(uri)
    guest,outsider = newcomer(uri),newcomer(uri)
    sender,receiver = (leader,guest) if kind=='invite' else (guest,leader)
    method,args = ('send_team_invite',(guest,)) if kind=='invite' else ('request_team_join',(team,))
    invitation = rpc(uri,method,*args,role='authenticated',user=sender)
    assert rpc(uri,method,*args,role='authenticated',user=sender)==invitation
    assert query(uri,'select team_id from public.profiles where id=%s',(guest,))==[(None,)]
    for who,direction in ((sender,'sent'),(receiver,'received')):
        rows=rpc(uri,'my_team_invitations',role='authenticated',user=who)
        assert len(rows)==1 and rows[0]['kind']==kind and rows[0]['direction']==direction
        assert rows[0]['status']=='pending' and rows[0]['unread']
        assert rpc(uri,'team_invitation_unread',role='authenticated',user=who)==1
        query(uri,'select public.mark_team_invitations_read(%s::uuid[],%s::timestamptz)',([invitation],rows[0]['updated_at']),role='authenticated',user=who)
        assert rpc(uri,'team_invitation_unread',role='authenticated',user=who)==0
    assert rpc(uri,'my_team_invitations',role='authenticated',user=outsider)==[]
    with pytest.raises(psycopg.Error,match='invitation_not_found'):
        rpc(uri,'respond_team_invite',invitation,True,role='authenticated',user=outsider)
    with pytest.raises(psycopg.Error,match='permission denied'):
        query(uri,'select * from private.team_invitations',role='authenticated',user=outsider)
    assert rpc(uri,'respond_team_invite',invitation,True,role='authenticated',user=receiver)=='accepted'
    assert rpc(uri,'respond_team_invite',invitation,True,role='authenticated',user=receiver)=='accepted'
    assert query(uri,'select team_id from public.profiles where id=%s',(guest,))==[(team,)]
    assert rpc(uri,'team_invitation_unread',role='authenticated',user=sender)==1
    query(uri,'select public.mark_team_invitations_read(%s::uuid[],%s::timestamptz)',([invitation],rows[0]['updated_at']),role='authenticated',user=sender)
    assert rpc(uri,'team_invitation_unread',role='authenticated',user=sender)==1  # stale page cannot hide new progress


def test_reject_cancel_and_closed_teams_never_change_membership(database):
    uri=database; leader,team=identity(uri); guest=newcomer(uri)
    invitation=rpc(uri,'request_team_join',team,role='authenticated',user=guest)
    assert rpc(uri,'respond_team_invite',invitation,False,role='authenticated',user=leader)=='declined'
    assert query(uri,'select team_id from public.profiles where id=%s',(guest,))==[(None,)]
    with pytest.raises(psycopg.Error,match='invitation_finished'):
        rpc(uri,'respond_team_invite',invitation,True,role='authenticated',user=leader)
    invitation=rpc(uri,'send_team_invite',guest,role='authenticated',user=leader)
    rpc(uri,'cancel_team_invite',invitation,role='authenticated',user=leader)
    assert rpc(uri,'my_team_invitations',role='authenticated',user=guest)[0]['status']=='cancelled'
    query(uri,'update public.teams set is_locked=true where id=%s',(team,))
    with pytest.raises(psycopg.Error,match='locked'):
        rpc(uri,'request_team_join',team,role='authenticated',user=guest)
    query(uri,'update public.teams set is_locked=false,is_hidden=true where id=%s',(team,))
    with pytest.raises(psycopg.Error,match='team_unavailable'):
        rpc(uri,'request_team_join',team,role='authenticated',user=guest)
    assert str(team) not in str(rpc(uri,'team_directory',role='anon'))
    query(uri,'update public.teams set is_hidden=false where id=%s',(team,))
    directory=[row[0] for row in query(uri,'select row_to_json(t) from public.team_directory() t',role='anon')]
    assert any(row['id']==str(team) for row in directory)
    assert 'invite_code' not in str(directory) and 'email' not in str(directory)


def test_two_acceptances_cannot_take_the_same_last_place(database):
    uri=database; leader,team=identity(uri)
    query(uri,'update public.teams set max_size=2 where id=%s',(team,))
    guests=[newcomer(uri),newcomer(uri)]
    invitations=[rpc(uri,'request_team_join',team,role='authenticated',user=user) for user in guests]
    def accept(invitation):
        try:return rpc(uri,'respond_team_invite',invitation,True,role='authenticated',user=leader)
        except psycopg.Error as exc:
            assert 'full' in str(exc)
            return 'full'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(accept,invitations))==['accepted','full']
    assert query(uri,'select count(*) from public.profiles where team_id=%s',(team,))==[(2,)]


def test_notification_history_paginates_and_new_captain_receives_pending_requests(database):
    uri=database;leader,team=identity(uri);next_leader,_=identity(uri,team=team);guest=newcomer(uri)
    request=rpc(uri,'request_team_join',team,role='authenticated',user=guest)
    query(uri,'update public.teams set leader_id=%s where id=%s',(next_leader,team))
    assert rpc(uri,'my_team_invitations',role='authenticated',user=leader)==[]
    rows=rpc(uri,'my_team_invitations',role='authenticated',user=next_leader)
    assert rows[0]['id']==str(request) and rows[0]['unread']
    with pytest.raises(psycopg.Error,match='invitation_not_found'):
        rpc(uri,'respond_team_invite',request,True,role='authenticated',user=leader)
    assert rpc(uri,'respond_team_invite',request,False,role='authenticated',user=next_leader)=='declined'
    query(uri,"""insert into private.team_invitations(team_id,team_name,sender_id,recipient_id,kind,status)
        select %s,'History',%s,%s,'request','declined' from generate_series(1,100)""",(team,guest,next_leader))
    first=rpc(uri,'my_team_invitations',role='authenticated',user=next_leader)
    assert len(first)==100
    last=first[-1]
    second=rpc(uri,'my_team_invitations',last['updated_at'],last['id'],role='authenticated',user=next_leader)
    assert len(second)==1
    assert len({r['id'] for r in first+second})==101
    for page in (first,second):
        query(uri,'select public.mark_team_invitations_read(%s::uuid[],%s::timestamptz)',
            ([r['id'] for r in page],max(r['updated_at'] for r in page)),role='authenticated',user=next_leader)
    assert rpc(uri,'team_invitation_unread',role='authenticated',user=next_leader)==0


def test_inbox_keeps_pending_requests_visible_until_answered(database):
    """Read or not, a pending request stays in the captain's inbox and badge count until it is answered."""
    uri=database; leader,team=identity(uri); guest,stale=newcomer(uri),newcomer(uri)
    counts=lambda who: rpc(uri,'team_notification_counts',role='authenticated',user=who)
    inbox=lambda who: rpc(uri,'my_team_inbox',role='authenticated',user=who)
    request=rpc(uri,'request_team_join',team,role='authenticated',user=guest)
    old=rpc(uri,'request_team_join',team,role='authenticated',user=stale)
    _,elsewhere=identity(uri); query(uri,'update public.profiles set team_id=%s where id=%s',(elsewhere,stale))
    rows=rpc(uri,'my_team_invitations',role='authenticated',user=leader)
    query(uri,'select public.mark_team_invitations_read(%s::uuid[],%s::timestamptz)',
        ([r['id'] for r in rows],max(r['updated_at'] for r in rows)),role='authenticated',user=leader)
    assert counts(leader)=={'pending':1,'unread':0}
    received={r['id']:r for r in inbox(leader)['received']}
    assert received[str(request)]['blocked'] is None and received[str(request)]['sender_name']
    assert received[str(old)]['blocked']=='joined_other_team'
    assert inbox(leader)['sent']==[]
    mine=inbox(guest)
    assert mine['received']==[] and [(r['id'],r['status']) for r in mine['sent']]==[(str(request),'pending')]
    assert counts(guest)['pending']==0
    assert rpc(uri,'respond_team_invite',request,True,role='authenticated',user=leader)=='accepted'
    assert counts(leader)['pending']==0 and [r['id'] for r in inbox(leader)['received']]==[str(old)]
    assert inbox(guest)['sent'][0]['status']=='accepted'
    rpc(uri,'respond_team_invite',old,False,role='authenticated',user=leader)
    assert inbox(leader)['received']==[]
    invitee=newcomer(uri); invite=rpc(uri,'send_team_invite',invitee,role='authenticated',user=leader)
    assert [r['id'] for r in inbox(leader)['sent']]==[str(invite)]
    assert counts(invitee)=={'pending':1,'unread':1} and inbox(invitee)['received'][0]['kind']=='invite'
    assert inbox(newcomer(uri))=={'received':[],'sent':[]}
    with pytest.raises(psycopg.Error,match='permission denied'):
        rpc(uri,'my_team_inbox',role='anon')
