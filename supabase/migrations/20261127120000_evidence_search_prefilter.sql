-- Step E: a database prefilter over the whole workspace for retrieval.
--
-- Retrieval ranked only the newest evidence records: 1000 for API-key and
-- Remote MCP reads (read_personal_api_key_evidence), and the PostgREST row
-- cap for the signed-in task-context read. An older record that answers the
-- query could never be selected once a workspace grew past that window.
--
-- This migration adds a full-text prefilter over the whole workspace. The
-- service unions its matches with the newest window and keeps ranking them
-- with the existing chunk scorer, so recent context is still always a
-- candidate and the final selection logic is unchanged.
--
-- * public.evidence_records gets a GIN expression index over
--   to_tsvector('simple', source_label || ' ' || content). The 'simple'
--   configuration does not stem and keeps identifiers and non-English text;
--   no table column is added, so no existing read or export changes.
-- * private.evidence_search_query turns free text into an OR query of at most
--   32 distinct terms (2..64 chars, English stop words dropped), built with
--   websearch_to_tsquery so arbitrary input can never raise a syntax error.
-- * public.search_workspace_evidence (SECURITY INVOKER) serves the signed-in
--   path: the caller's own RLS decides what it can match.
-- * public.read_personal_api_key_evidence_candidates serves API-key and
--   Remote MCP retrieval in ONE signed call (so a retrieval still counts once
--   against the key's 60/minute limit). It runs the unchanged
--   private.authorize_personal_api_key_read('evidence', ...) check, then
--   returns the newest p_limit records plus up to p_search_limit full-text
--   matches. The query text narrows rows the key may already read; it is not
--   an authorization input.
--
-- Matches are ranked with ts_rank over at most the 5000 newest matching rows
-- of the workspace, which bounds the cost for very common terms.

create index evidence_records_search_idx
    on public.evidence_records
    using gin (
        pg_catalog.to_tsvector(
            'pg_catalog.simple'::pg_catalog.regconfig,
            coalesce(source_label, '') || ' ' || content
        )
    );

create function private.evidence_search_query(p_query text)
returns tsquery
language sql
stable
set search_path = ''
as $function$
    select pg_catalog.websearch_to_tsquery(
        'pg_catalog.simple'::pg_catalog.regconfig,
        pg_catalog.string_agg(term.lexeme, ' or ')
    )
    from (
        select lexeme
        from pg_catalog.unnest(pg_catalog.tsvector_to_array(pg_catalog.to_tsvector(
            'pg_catalog.simple'::pg_catalog.regconfig,
            pg_catalog.left(coalesce(p_query, ''), 2000)
        ))) as lexeme
        where pg_catalog.length(lexeme) between 2 and 64
          and pg_catalog.length(pg_catalog.to_tsvector(
              'pg_catalog.english'::pg_catalog.regconfig, lexeme
          )) > 0
        limit 32
    ) as term
$function$;

create function public.search_workspace_evidence(
    p_workspace_id uuid,
    p_query text,
    p_limit integer default 200
)
returns table (
    id uuid,
    workspace_id uuid,
    content text,
    source_label text,
    source_id uuid,
    source_version_id uuid,
    created_at timestamptz,
    updated_at timestamptz
)
language plpgsql
stable
security invoker
set search_path = ''
as $function$
declare
    v_query tsquery := private.evidence_search_query(p_query);
begin
    if p_workspace_id is null or p_limit is null or p_limit < 1 or p_limit > 500 then
        raise exception 'invalid evidence search' using errcode = '22023';
    end if;
    if v_query is null or pg_catalog.numnode(v_query) = 0 then
        return;
    end if;
    return query
    select matched.id, matched.workspace_id, matched.content, matched.source_label,
           matched.source_id, matched.source_version_id, matched.created_at, matched.updated_at
      from (
          select record.id, record.workspace_id, record.content, record.source_label,
                 record.source_id, record.source_version_id, record.created_at, record.updated_at
            from public.evidence_records as record
           where record.workspace_id = p_workspace_id
             and pg_catalog.to_tsvector(
                     'pg_catalog.simple'::pg_catalog.regconfig,
                     coalesce(record.source_label, '') || ' ' || record.content
                 ) @@ v_query
           order by record.created_at desc, record.id desc
           limit 5000
      ) as matched
     order by pg_catalog.ts_rank(
                  pg_catalog.to_tsvector(
                      'pg_catalog.simple'::pg_catalog.regconfig,
                      coalesce(matched.source_label, '') || ' ' || matched.content
                  ),
                  v_query, 1
              ) desc,
              matched.created_at desc, matched.id desc
     limit p_limit;
end;
$function$;

create function public.read_personal_api_key_evidence_candidates(
    p_key_id uuid,
    p_workspace_id uuid,
    p_limit integer,
    p_query text,
    p_search_limit integer default 200
)
returns table (
    id uuid,
    workspace_id uuid,
    content text,
    source_label text,
    source_id uuid,
    source_version_id uuid,
    created_at timestamptz,
    updated_at timestamptz
)
language plpgsql
security definer
set search_path = ''
set row_security = on
as $function$
declare
    v_query tsquery;
begin
    perform private.authorize_personal_api_key_read(
        'evidence', p_key_id, p_workspace_id, p_limit, null, null
    );
    if p_search_limit is null or p_search_limit < 0 or p_search_limit > 500 then
        raise exception 'invalid evidence search' using errcode = '22023';
    end if;
    v_query := private.evidence_search_query(p_query);
    return query
    with newest as (
        select record.id, record.workspace_id, record.content, record.source_label,
               record.source_id, record.source_version_id, record.created_at, record.updated_at
          from public.evidence_records as record
         where record.workspace_id = p_workspace_id
         order by record.created_at desc, record.id desc
         limit p_limit
    ),
    matched as (
        select record.id, record.workspace_id, record.content, record.source_label,
               record.source_id, record.source_version_id, record.created_at, record.updated_at
          from public.evidence_records as record
         where p_search_limit > 0
           and v_query is not null
           and pg_catalog.numnode(v_query) > 0
           and record.workspace_id = p_workspace_id
           and pg_catalog.to_tsvector(
                   'pg_catalog.simple'::pg_catalog.regconfig,
                   coalesce(record.source_label, '') || ' ' || record.content
               ) @@ v_query
         order by record.created_at desc, record.id desc
         limit 5000
    ),
    ranked as (
        select matched.*
          from matched
         where not exists (select 1 from newest where newest.id = matched.id)
         order by pg_catalog.ts_rank(
                      pg_catalog.to_tsvector(
                          'pg_catalog.simple'::pg_catalog.regconfig,
                          coalesce(matched.source_label, '') || ' ' || matched.content
                      ),
                      v_query, 1
                  ) desc,
                  matched.created_at desc, matched.id desc
         limit p_search_limit
    )
    select * from newest
    union all
    select * from ranked;
end;
$function$;

revoke all on function private.evidence_search_query(text) from public, anon, authenticated, service_role;
grant execute on function private.evidence_search_query(text) to authenticated, personal_api_key_reader;

revoke all on function public.search_workspace_evidence(uuid, text, integer)
    from public, anon, service_role;
grant execute on function public.search_workspace_evidence(uuid, text, integer) to authenticated;

revoke all on function public.read_personal_api_key_evidence_candidates(uuid, uuid, integer, text, integer)
    from public, authenticated, service_role;
grant execute on function public.read_personal_api_key_evidence_candidates(uuid, uuid, integer, text, integer)
    to anon;

-- The signed read runs as personal_api_key_reader, like
-- read_personal_api_key_evidence, so its RLS policies and column grants apply.
-- ALTER OWNER needs CREATE on the schema for the new owner and SET on the role
-- for the migration role; both are granted for this statement only.
grant create on schema public to personal_api_key_reader;

do $owners$
declare
    actor name := current_user;
begin
    execute pg_catalog.format(
        'grant personal_api_key_reader to %I with set true, inherit false', actor
    );
    execute 'alter function public.read_personal_api_key_evidence_candidates(uuid,uuid,integer,text,integer) owner to personal_api_key_reader';
    execute pg_catalog.format('revoke personal_api_key_reader from %I', actor);
end;
$owners$;

revoke create on schema public from personal_api_key_reader;
