from __future__ import annotations

import secrets

import httpx


def _assert_one_available_lifecycle_row(stack, user_id: str) -> None:
    stack.sql(
        "do $$ begin "
        "if (select count(*) from auth.users where id = '" + user_id + "') <> 1 "
        "or (select count(*) from public.account_lifecycle where subject_id = '"
        + user_id
        + "' and state = 'available' and row_version = 1 "
        "and transition_actor = 'auth_provisioning' "
        "and transition_reason = 'auth_identity_created') <> 1 then "
        "raise exception 'Auth lifecycle provisioning assertion failed'; "
        "end if; end $$"
    )


def test_new_auth_identity_gets_one_lifecycle_row_and_only_self_projection(stack):
    token, user_id = stack.signup("lifecycle-provisioned")
    _other_token, other_id = stack.signup("lifecycle-other")
    _assert_one_available_lifecycle_row(stack, user_id)

    own = stack.client.get(
        f"{stack.api}/rest/v1/account_lifecycle",
        params={"select": "subject_id,state,row_version"},
        headers=stack.headers(token),
    )
    assert own.status_code == 200
    assert own.json() == [
        {"subject_id": user_id, "state": "available", "row_version": 1}
    ]

    other = stack.client.get(
        f"{stack.api}/rest/v1/account_lifecycle",
        params={
            "select": "subject_id,state,row_version",
            "subject_id": f"eq.{other_id}",
        },
        headers=stack.headers(token),
    )
    assert other.status_code == 200
    assert other.json() == []

    internal = stack.client.get(
        f"{stack.api}/rest/v1/account_lifecycle",
        params={"select": "request_id,transition_actor,transition_reason"},
        headers=stack.headers(token),
    )
    assert internal.status_code in {400, 401, 403}
    assert not internal.is_success

    mutation = stack.client.patch(
        f"{stack.api}/rest/v1/account_lifecycle",
        params={"subject_id": f"eq.{user_id}"},
        headers=stack.headers(
            token,
            **{"content-type": "application/json", "prefer": "return=minimal"},
        ),
        json={"state": "revocation_pending"},
    )
    assert mutation.status_code in {400, 401, 403}


def test_auth_signup_rolls_back_when_lifecycle_provisioning_is_unavailable(stack):
    email = f"lifecycle-failed-{secrets.token_hex(8)}@example.invalid"
    password = secrets.token_urlsafe(32)
    stack.sql("drop policy account_lifecycle_insert_provisioner on public.account_lifecycle")
    response: httpx.Response | None = None
    try:
        response = stack.client.post(
            f"{stack.api}/auth/v1/signup",
            headers={"apikey": stack.key},
            json={"email": email, "password": password},
        )
    finally:
        stack.sql(
            "create policy account_lifecycle_insert_provisioner "
            "on public.account_lifecycle for insert to account_lifecycle_provisioner "
            "with check (state = 'available' and row_version = 1 "
            "and request_id is null and "
            "transition_actor = 'auth_provisioning' "
            "and transition_reason = 'auth_identity_created')"
        )

    assert response is not None and response.status_code >= 400
    escaped_email = email.replace("'", "''")
    stack.sql(
        "do $$ begin "
        "if exists (select 1 from auth.users where email = '"
        + escaped_email
        + "') or exists (select 1 from public.account_lifecycle as lifecycle "
        "join auth.users as users on users.id = lifecycle.subject_id "
        "where users.email = '"
        + escaped_email
        + "') then raise exception 'failed Auth provisioning left an identity'; "
        "end if; end $$"
    )
