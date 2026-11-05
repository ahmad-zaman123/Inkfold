import psycopg
import pytest

from conftest import sign_in

PNG = b"\x89PNG\r\n\x1a\n" + b"card-bytes"


def upload(client, auth, data=PNG, name="card.png"):
    response = client.post("/api/documents", files={"file": (name, data, "application/octet-stream")}, headers=auth)
    assert response.status_code in (200, 201), response.text
    return response.json()


def invite(client, auth, email):
    response = client.post("/api/team/invites", json={"email": email}, headers=auth)
    assert response.status_code == 201, response.text
    return response.json()["invite"]


def token_of(invite):
    return invite["link"].rsplit("/", 1)[1]


def join(client, auth, invite):
    return client.post(f"/api/invites/{token_of(invite)}/accept", headers=auth)


@pytest.fixture
def team(client):
    """An owner and a member who joined through an invite."""
    owner = sign_in(client, "sara@example.com")
    member = sign_in(client, "omar@example.com")
    assert join(client, member, invite(client, owner, "Omar@Example.com")).status_code == 200
    return owner, member


def test_every_account_starts_in_a_team_of_its_own(client, auth):
    me = client.get("/api/me", headers=auth).json()
    assert me["team"] == {"role": "owner", "size": 1}
    team = client.get("/api/team", headers=auth).json()
    assert team["role"] == "owner" and team["can_invite"] is True
    assert [(m["email"], m["role"], m["you"]) for m in team["members"]] == [("sara@example.com", "owner", True)]


def test_teammates_review_the_same_documents(client, team, services):
    owner, member = team
    document = upload(client, owner)
    assert document["added_by"] == "sara@example.com"
    listed = client.get("/api/documents", headers=member).json()["documents"]
    assert [d["id"] for d in listed] == [document["id"]] and listed[0]["added_by"] == "sara@example.com"
    # The member can edit it, see its original and send it, and the history says who sent it.
    edited = client.patch(f"/api/documents/{document['id']}/fields", json={"fields": {"contact.job_title": "Ops lead"}}, headers=member).json()
    assert edited["fields"]["contact.job_title"]["edited_by"] == "omar@example.com"
    assert client.get(f"/api/documents/{document['id']}/file", headers=member).content == PNG
    sent = client.post(f"/api/documents/{document['id']}/sync", headers=member).json()
    assert sent["status"] == "synced" and sent["sync_events"][-1]["actor"] == "omar@example.com"
    for auth in team:
        assert client.get("/api/me", headers=auth).json()["team"]["size"] == 2


def test_a_file_the_team_already_has_is_a_duplicate_for_everyone(client, team, gemini):
    owner, member = team
    first = upload(client, owner)
    again = upload(client, member)
    assert again["duplicate"] is True and again["id"] == first["id"] and gemini.calls == 1


def test_joining_brings_your_documents_but_not_copies_the_team_has(client, database_url):
    owner = sign_in(client, "sara@example.com")
    newcomer = sign_in(client, "omar@example.com")
    shared = upload(client, owner)
    upload(client, newcomer)  # the same file, which the team already has
    own = upload(client, newcomer, data=PNG + b"-only-omar", name="omar.png")
    response = join(client, newcomer, invite(client, owner, "omar@example.com"))
    assert response.status_code == 200 and response.json()["moved"] == 1
    ids = sorted(d["id"] for d in client.get("/api/documents", headers=owner).json()["documents"])
    assert ids == sorted([shared["id"], own["id"]])
    with psycopg.connect(database_url) as db:
        # The newcomer's team of one is gone, not left behind empty.
        assert db.execute("SELECT count(*) FROM teams").fetchone()[0] == 1


def test_invite_links_say_who_they_are_from_and_work_once(client):
    owner = sign_in(client, "sara@example.com")
    client.patch("/api/team", json={"name": "Northwind Sales"}, headers=owner)
    sent = invite(client, owner, "omar@example.com")
    assert sent["emailed"] is False and sent["link"].startswith("http://localhost:5180/join/")
    info = client.get(f"/api/invites/{token_of(sent)}").json()
    assert info == {"team": "Northwind Sales", "invited_by": "sara@example.com", "email": "omar@example.com", "team_size": 1}
    assert [i["email"] for i in client.get("/api/team", headers=owner).json()["invites"]] == ["omar@example.com"]
    member = sign_in(client, "omar@example.com")
    assert join(client, member, sent).status_code == 200
    assert join(client, member, sent).status_code == 404
    assert client.get(f"/api/invites/{token_of(sent)}").status_code == 404
    assert client.get("/api/team", headers=owner).json()["invites"] == []


def test_an_invite_only_works_for_its_email(client):
    owner = sign_in(client, "sara@example.com")
    sent = invite(client, owner, "omar@example.com")
    stranger = sign_in(client, "mallory@example.com")
    response = join(client, stranger, sent)
    assert response.status_code == 403 and "different email" in response.json()["detail"]
    assert client.get("/api/documents", headers=stranger).json()["documents"] == []


def test_invites_expire_and_can_be_cancelled(client, database_url):
    owner = sign_in(client, "sara@example.com")
    member = sign_in(client, "omar@example.com")
    first = invite(client, owner, "omar@example.com")
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute("UPDATE team_invites SET expires_at = now() - interval '1 minute'")
    assert join(client, member, first).status_code == 404
    second = invite(client, owner, "omar@example.com")
    assert client.delete(f"/api/team/invites/{second['id']}", headers=owner).status_code == 200
    assert join(client, member, second).status_code == 404
    # Inviting again replaces the old link rather than adding another.
    invite(client, owner, "omar@example.com")
    newest = invite(client, owner, "omar@example.com")
    assert len(client.get("/api/team", headers=owner).json()["invites"]) == 1
    assert join(client, member, newest).status_code == 200


def test_invites_are_emailed_when_email_is_set_up(build, mailer):
    client = build(send_email=mailer)
    owner = sign_in(client, "sara@example.com")
    sent = invite(client, owner, "omar@example.com")
    assert sent["emailed"] is True
    assert mailer.sent[0].to == "omar@example.com" and mailer.sent[0].url == sent["link"]
    assert "sara@example.com" in mailer.sent[0].subject


def test_only_the_owner_manages_the_team(client, team):
    owner, member = team
    assert client.post("/api/team/invites", json={"email": "lina@example.com"}, headers=member).status_code == 403
    assert client.patch("/api/team", json={"name": "Mine now"}, headers=member).status_code == 403
    owner_id = next(m["id"] for m in client.get("/api/team", headers=owner).json()["members"] if m["you"])
    assert client.delete(f"/api/team/members/{owner_id}", headers=member).status_code == 403
    assert client.get("/api/team", headers=member).json()["invites"] == []
    assert client.post("/api/team/invites", json={"email": "omar@example.com"}, headers=owner).status_code == 409


def test_leaving_or_being_removed_ends_access_but_documents_stay(client, team):
    owner, member = team
    document = upload(client, member)
    left = client.post("/api/team/leave", headers=member)
    assert left.status_code == 200 and left.json()["role"] == "owner" and len(left.json()["members"]) == 1
    assert client.get("/api/documents", headers=member).json()["documents"] == []
    assert client.get(f"/api/documents/{document['id']}", headers=member).status_code == 404
    assert [d["id"] for d in client.get("/api/documents", headers=owner).json()["documents"]] == [document["id"]]
    # Back in, then removed by the owner.
    assert join(client, member, invite(client, owner, "omar@example.com")).status_code == 200
    member_id = next(m["id"] for m in client.get("/api/team", headers=owner).json()["members"] if not m["you"])
    assert client.delete(f"/api/team/members/{member_id}", headers=owner).status_code == 200
    assert client.get("/api/documents", headers=member).json()["documents"] == []
    assert client.post("/api/team/leave", headers=owner).status_code == 409


def test_an_owner_with_teammates_cannot_join_another_team(client, team):
    owner, _ = team
    other = sign_in(client, "lina@example.com")
    response = join(client, owner, invite(client, other, "sara@example.com"))
    assert response.status_code == 409 and "Remove them" in response.json()["detail"]


def test_demo_accounts_cannot_invite_or_join(client):
    demo = {"Authorization": "Bearer " + client.post("/api/auth/demo").json()["token"]}
    assert client.get("/api/team", headers=demo).json()["can_invite"] is False
    assert client.post("/api/team/invites", json={"email": "omar@example.com"}, headers=demo).status_code == 403
    owner = sign_in(client, "sara@example.com")
    sent = invite(client, owner, "omar@example.com")
    assert join(client, demo, sent).status_code == 403


def test_a_save_based_on_an_old_version_is_refused(client, team):
    owner, member = team
    document = upload(client, owner)
    base = document["updated_at"]
    first = client.patch(
        f"/api/documents/{document['id']}/fields",
        json={"fields": {"contact.job_title": "Ops lead"}, "base_updated_at": base}, headers=owner,
    )
    assert first.status_code == 200
    late = client.patch(
        f"/api/documents/{document['id']}/fields",
        json={"fields": {"contact.job_title": "Head of ops"}, "base_updated_at": base}, headers=member,
    )
    assert late.status_code == 409 and "Someone else saved" in late.json()["detail"]
    current = client.get(f"/api/documents/{document['id']}", headers=member).json()
    assert current["fields"]["contact.job_title"]["value"] == "Ops lead"
    retried = client.patch(
        f"/api/documents/{document['id']}/fields",
        json={"fields": {"contact.job_title": "Head of ops"}, "base_updated_at": current["updated_at"]}, headers=member,
    )
    assert retried.status_code == 200


def test_accounts_from_before_teams_keep_their_documents(store, database_url):
    """The schema runs again on an existing database: a team per account, and documents stay put."""
    with psycopg.connect(database_url, autocommit=True) as db:
        db.execute("INSERT INTO users (id, email) VALUES ('00000000-0000-0000-0000-000000000001', 'old@example.com')")
        db.execute(
            "INSERT INTO documents (id, user_id, filename, mime, size, sha256, doc_type, status, fields) VALUES "
            "(gen_random_uuid(), '00000000-0000-0000-0000-000000000001', 'a.png', 'image/png', 1, 'abc', 'other', 'needs_review', '{}')"
        )
        db.execute("UPDATE users SET team_id = NULL")
        db.execute("DELETE FROM teams")
    store.init_schema()
    with psycopg.connect(database_url) as db:
        assert db.execute("SELECT team_id::text, team_role FROM users").fetchall() == [
            ("00000000-0000-0000-0000-000000000001", "owner")
        ]
        assert db.execute("SELECT team_id::text FROM documents").fetchall() == [("00000000-0000-0000-0000-000000000001",)]
