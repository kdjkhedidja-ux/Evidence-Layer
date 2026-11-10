-- The authenticated self-read policy on personal_api_keys evaluates the
-- request subject through this helper. It only reads JWT claims; the grant
-- does not expose key material or workspace data.
grant execute on function private.personal_api_key_subject() to authenticated;
