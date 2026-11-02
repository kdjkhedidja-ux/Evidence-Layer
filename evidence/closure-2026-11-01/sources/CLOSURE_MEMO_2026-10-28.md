# محضر إغلاق المرحلة — 2026-10-28

محضر فعلي لما نُفِّذ وما بقي، مدعوم بأدلة من المستودع وGitHub Actions.
لا يتضمن أي تفعيل نشر إنتاجي أو تطبيق ترحيلات على مشروع مستضاف.

## 1. ما نُفِّذ

1. **دمج PR #28** (squash) إلى `main` بعد تأكيد أن الفحوص المطلوبة (`workspace-rls`,
   `test-and-build`, `local-validation`) خضراء على أحدث حالة للفرع `8a377e73`.
   - Merge commit: `8f31575` — "ci: guard Supabase migration deployment (#28)".
2. **إعادة تأسيس** `fix/rls-auto-enable-fail-closed` على `origin/main` → `0c1aae8`
   (إعادة تأسيس نظيفة بلا تعارضات).
3. **تسجيل الترحيل مع الحارس**: تطبيق `PR28_MIGRATION_CORRECTION.patch` (البيان + سجل
   الجمل + تعديل 6 أعداد في اختبارات الحارس) وتحديث أعداد `docs/SUPABASE_MIGRATION_RELEASE.md`
   → commit `d17a667` (4 ملفات، ‎+25/−10).
4. **دفع الفرع وفتح PR #39** بعنوان
   "fix(security): fail closed when the RLS bootstrap cannot enable RLS".

## 2. نتائج الفحوص على PR #39 (head `d17a667`)

| الفحص | النتيجة |
| --- | --- |
| `workspace-rls` | **pass** — pgTAP `Files=7, Tests=366, Result: PASS` |
| `local-validation` | **pass** — `validate` (12 ملفًا) + `classify` (192 جملة / 156 مسجّلة / 0 محظورة) + 58 اختبار حارس `OK` |
| `test-and-build` | pass |
| `web` | pass |
| `auth-ui-browser` | pass |
| Vercel | pass |
| `production-plan` / `production-apply` | skipping (معطّلان قصدًا بـ `&& false`) |
| حالة الدمج | `CLEAN` / `MERGEABLE` |

بهذا اجتاز إصلاح `rls_auto_enable()` الاختبارات المتكاملة فعليًا في CI
(366 = 362 سابقة + 4 تأكيدات fail-closed جديدة)، بعد أن كان غير مُختبر سابقًا
لأن الفرع لم يكن مدفوعًا.

## 3. ما أُصلح خلال المراجعة (تصحيح ذاتي موثّق)

- `workspace-rls` كان **ناجحًا** في CI (وليس فاشلًا كما ظُنّ أولًا).
- ملكية event trigger = الدور المُنشئ، لا bootstrap superuser بالضرورة؛
  `evtowner=supabase_admin` محليًا أثر bootstrap لا عيب ترحيل/CLI/صورة.
- لا يوجد "تعارض مرجع مشروع" بين workflow والوثائق: `slxfsjnsrvfkdtxayiud`
  (Staging لاختبار Auth) يختلف وظيفيًا عن `ayrtyjgqlgtuiwuyigbz` (هدف الترحيلات).
- الحارس يحتاج **6** تأكيدات عددية عند إضافة ترحيل (وليس تعديل البيان/السجل فقط).

## 4. حماية الفرع والبيئة (الوضع الحالي)

- ruleset `main-protection` (id `25967209`) — enforcement `active`، يستهدف
  `~DEFAULT_BRANCH`، ويتطلب: `workspace-rls`, `test-and-build`, `local-validation`.
- بيئة `Production` على GitHub: `deployment_branch_policy` = `main` فقط،
  `protection_rules = [branch_policy]` فقط، **`can_admins_bypass = true`**،
  **لا required reviewers** ⇒ البوابة #2 في `docs/SUPABASE_MIGRATION_RELEASE.md`
  غير مستوفاة بعد.
- أسرار/متغيرات المستودع غير قابلة للتحقق بالتوكن الحالي (HTTP 403).

## 5. النسخ الاحتياطي والاستعادة

- آخر حالة مسجّلة (2026-10-19): **لا نسخة مكتملة، PITR معطّل** ⇒ لا نقطة استعادة مُثبتة.
- لا يمكن إعادة التحقق الآن: `SUPABASE_ACCESS_TOKEN` غير موجود في البيئة،
  و`GET /v1/projects/ayrtyjgqlgtuiwuyigbz/database/backups` يعيد `401`.
- المعلومة الدقيقة المطلوبة (لا أكثر):
  - API: `pitr_enabled`, `walg_enabled`, `backups[].status/inserted_at`,
    `physical_backup_data.latest_physical_backup_date_unix`,
    `physical_backup_data.earliest_physical_backup_date_unix`.
  - أو Dashboard → Database → Backups (قائمة النسخ + نافذة PITR) + **اسم المالك
    المخوّل بطلب الاستعادة**.
- لا يمكن إثبات الاستعادة اليوم؛ الحارس `recovery_is_recent` يشترط نسخة/PITR ≤ 48 ساعة،
  وإلا رفض `plan`. أي إثبات استعادة يتطلب تمرينًا على مشروع منفصل (الاستعادة في
  Supabase تُعيد كتابة الهدف).

## 6. ما تبقّى من عوائق فعلية (قرارات مالك المستودع)

1. **دمج PR #39** — جاهز (`CLEAN`/`MERGEABLE`، كل الفحوص خضراء)؛ لم يُدمج انتظارًا للقرار.
2. **بيئة `Production`**: تفعيل required reviewers وإيقاف `can_admins_bypass`
   (وإلا تبقى بوابة النشر غير مستوفاة حتى مع بقاء النشر معطّلًا).
3. **نقطة استعادة**: الحصول على الحقول أعلاه لفتح `plan` (أو إبقاء التخطيط مغلقًا).
4. (اختياري) تأكيد حيّ واحد لـ `evtowner` في المشروع المستضاف
   (`production_history_query.sql`) عند توفر بيانات الدخول.
5. عدم تنفيذ أي تطبيق ترحيلات إنتاجي أو تفعيل نشر قبل البنود أعلاه.
