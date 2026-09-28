# EndNote and Cite While You Write

Read this before touching citations, the bibliography or an EndNote library: how Cite While You
Write (CWYW) stores citations in a .docx, the author's division of labour, the conversion
procedure for when the author asks for it, and the failures it has produced before.

## The author's rule: hands off

By default the author runs EndNote.

- Cite new or changed text with placeholders: `[PMID: 12345678]`, `[PMIDs: 12345678, 23456789]`,
  or `[REF: key]` for a source without a PMID (dataset, web page, book). Insert them as tracked
  text like any other edit, after verifying each PMID ([reference audit](reference-audit.md)).
- The author converts them in Word: select the record in EndNote, Insert Selected Citation
  (Alt+2 on Windows), then Update Citations and Bibliography.
- Never edit, regenerate, reorder, unlink, restyle or "clean up" an EndNote field or the
  bibliography. They are atoms: text may change around them, never inside them. The gate's
  `citations`, `links`, `bookmarks` and `fields` checks prove this on every build.
- Report the author's steps in this order: refresh EndNote (Update Citations and Bibliography),
  then the table of contents, then any page numbers written as plain text.
- A wrong reference is a comment or a row in the hand-off list, not an edit. See the replacement
  table in [reference audit](reference-audit.md).
- Reference-list spacing belongs to EndNote. In Word, EndNote tab > Configure Bibliography (Format
  Bibliography) > Layout sets Line spacing and Space after, and every Update Citations and
  Bibliography writes them into the bibliography paragraphs. The line spacing the builder gives
  its `EndNote Bibliography` style lasts only until EndNote first formats the list. Ask the author
  to set Layout from the profile's `formatting.references_line_spacing` (and the journal's space
  after) before the next update; never fix the spacing with a tracked edit or a style change.
  `msw.py lint` reports a mismatch as `format.reference-spacing`, a reminder to check that
  setting.

Default CWYW shortcuts in Word on Windows: Alt+1 switches to EndNote, Alt+2 inserts the selected
citation, Alt+3 formats the bibliography, Alt+4 unformats citations, Alt+6 edits citations, Alt+7
finds citations. Update Citations and Bibliography has none. Users can reassign them, so confirm
the author's before relying on one
([EndNote documentation](https://docs.endnote.com/docs/endnote/2025/v1/windows/en/content/appendices/cwyw_keyboard_commands.htm)).

Run the conversion procedure only when the author asks an agent to convert placeholders or
rebuild the bibliography, or when lost fields must be relinked.

## Anatomy in the .docx

| Part | Content |
| --- | --- |
| `word/document.xml` | Every `ADDIN EN.CITE`, `EN.CITE.DATA` and `EN.REFLIST` field, the `_ENREF_n` bookmarks and the citation links. |
| `word/settings.xml`, `w:docVars` | `EN.InstantFormat` (`Enabled`, `ScanUnformatted`, `ScanChanges`, `Suspended`); `EN.Layout` (style, `LeftDelim`/`RightDelim`, font, size, first number, indents, `LineSpacing` and `SpaceAfter` of the reference list); `EN.Libraries` (library name, db-id, record ids). |
| `word/settings.xml` | `w:trackRevisions` and `w:doNotTrackFormatting`. |
| `word/endnotes.xml` | Word's own endnotes; unrelated to the EndNote program. |

Read the docVars for the delimiters and Instant Formatting (`msw.py inspect`); never edit them.

### A citation is a nested complex field

One citation group is one outer field whose instruction starts with ` ADDIN EN.CITE`. It may hold
zero, one or several nested `ADDIN EN.CITE.DATA` fields, a multi-run result, and links to
`_ENREF_n` bookmarks. Parse with a field stack that runs across the whole document
(`mswlib.wordml.map_fields`). A per-paragraph walk, a substring count of `ADDIN EN.CITE` (which
also counts `EN.CITE.DATA`) or balanced begin and end counts all give wrong answers.

Record data comes in three forms. The skeletons are synthetic; run properties are elided.

Form A, inline XML (short groups):

```xml
<w:r><w:fldChar w:fldCharType="begin"/></w:r>
<w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE &lt;EndNote&gt;&lt;Cite&gt;...&lt;/Cite&gt;&lt;/EndNote&gt;</w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:t>(</w:t></w:r>
<w:hyperlink w:anchor="_ENREF_5" w:tooltip="Doe, 2020 #12"><w:r><w:t>5</w:t></w:r></w:hyperlink>
<w:r><w:t>)</w:t></w:r>
<w:r><w:fldChar w:fldCharType="end"/></w:r>
```

Form B, one base64 blob (common), repeated in one nested EN.CITE.DATA field with no separator:

```xml
<w:r><w:fldChar w:fldCharType="begin"><w:fldData xml:space="preserve">PEVuZE5vdGU+...</w:fldData></w:fldChar></w:r>
<w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE </w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="begin"><w:fldData xml:space="preserve">PEVuZE5vdGU+...</w:fldData></w:fldChar></w:r>
<w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE.DATA </w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="end"/></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>
<!-- result runs and links as in form A -->
<w:r><w:fldChar w:fldCharType="end"/></w:r>
```

Form C, several chunks (very long groups): k nested EN.CITE.DATA fields in document order, each
chunk padded on its own; the outer `fldData` repeats only the last chunk.

**Decoding rule:** decode each nested chunk separately, join the bytes in document order, strip
trailing NUL bytes, then parse. Joining the base64 strings first fails at the first padding, and
decoding only the outer or first blob silently loses records. With no nested chunks, decode the
outer blob; with no blob, take `<EndNote>...</EndNote>` from the joined instruction text. The
base64 is wrapped with CR/LF, so a text-mode rewrite of `document.xml` corrupts it.
`mswlib.wordml.endnote_payload` implements this rule.

### What a payload holds

```xml
<EndNote><Cite><Author>Doe</Author><Year>2020</Year><RecNum>12</RecNum><DisplayText>(3)</DisplayText>
  <record><rec-number>12</rec-number><foreign-keys><key app="EN" db-id="abcd1234">12</key></foreign-keys>
    <ref-type name="Journal Article">17</ref-type>
    <contributors><authors><author>Doe, J.</author></authors></contributors>
    <titles><title>Marker Q-17 in a synthetic cohort</title><secondary-title>Example J</secondary-title></titles>
    <pages>101-110</pages><volume>12</volume><dates><year>2020</year></dates>
    <accession-num>12345678</accession-num><electronic-resource-num>10.1000/example.12</electronic-resource-num>
  </record></Cite></EndNote>
```

- A group with several references is one field with several `<Cite>` elements.
- PubMed imports keep the PMID in `accession-num` (often with `remote-database-name` empty) and
  the DOI in `electronic-resource-num`.
- Record numbers are unique only inside one library: key records by `(db-id, rec-number)` and
  identify papers by DOI or PMID.
- Read text with `itertext()`; a library's XML export wraps values in `<style>` children.

### Links before and after a Word save

- Before Word saves the file, EndNote writes each linked number as
  `<w:hyperlink w:anchor="_ENREF_n" w:tooltip="Author, Year #rec">`.
- After a Word save, the same links are nested fields: `HYPERLINK \l "_ENREF_n" \o "..."`. A file
  edited after a save can hold both forms. Count both and check that every target bookmark
  exists; `msw.py census` does.
- Ranges such as 3-5 give fewer links than occurrences. A style with links off has no `_ENREF`
  bookmarks, so link checks depend on the style.
- Result runs keep the character formatting of the text they replaced, a highlight included.

### The bibliography field spans paragraphs

`ADDIN EN.REFLIST` begins in the first bibliography paragraph and ends in a trailing paragraph
holding only `fldChar end`. Each entry is an `EndNoteBibliography` paragraph: `bookmarkStart
_ENREF_n`, number, tab, entry, `bookmarkEnd`. A per-paragraph field walk misses it, a cleanup of
"empty" paragraphs must skip any with a `fldChar`, and a moved end marker swallows everything
after it, figures included.

### Temporary citations

- Syntax `{Author, Year #RecNum}`; a group is `{Doe, 2020 #12;Roe, 2019 #7}`. The delimiters come
  from `EN.Layout` `LeftDelim`/`RightDelim`; read them rather than assuming braces.
- Put a backtick before a literal `;` or `\` inside a temporary citation.
- `#RecNum` refers to the library open when EndNote formats. Merging libraries renumbers records
  and invalidates every temporary citation.
- When author, year and record disagree, EndNote opens a modal Select Matching Reference dialog,
  so formatting needs an interactive Word session.
- A temporary citation is one indivisible token: no revision boundary may split it.

## Commands that help

| Command | Use |
| --- | --- |
| `msw.py census <docx> [--full]` | Live and deleted citation fields, payload forms, occurrences, distinct `(db-id, rec)`, db-ids, bibliography spans with any drawings or headings inside, `_ENREF` bookmarks, links, missing targets, placeholders (`--full` lists the PMIDs). Exits 1 on field errors or missing link targets. |
| `msw.py inspect <docx>` | Instant Formatting state, track-revisions flag, Word lock files, highest revision id. |
| `msw.py refs placeholders <docx> [--json [OUT]]` | Every `[PMID: n]`, `[PMIDs: ...]`, `[REF: key]` and temporary citation with its paragraph; marks a placeholder split across runs. Exits 1 on a malformed placeholder. |
| `msw.py refs pubmed --from-docx <docx> --out <dir>` | PubMed records with archived evidence, for the identity check. |
| `msw.py refs census <docx> [--json OUT]` | The census plus the payload metadata audit (missing pages, DOI or PMID, corporate authors, collisions). |
| `msw.py refs library-map <export.xml> --out <map.json>` | PMID to record number from an EndNote XML export of one library (PMID from `accession-num`, else a PubMed URL); a record's EndNote Label resolves `[REF: key]`. Exits 1 on duplicate PMIDs or several db-ids. |
| `msw.py refs temp-citations <docx> --map <map.json> --out <new.docx>` | Placeholders to temporary citations on a clean copy, with the document's delimiters and escaping, plus a ledger (`<out stem>.temp-citations.json`, or `--ledger`). |
| `msw.py verify <source> <candidate>` | The gate; `--allow-part-changes` for a Word-saved round trip. |
| `msw.py word-qa <docx> --out <dir>` | Word's own accept and reject compared with the file views, in an isolated, read-only Word instance. |

No command writes a native EndNote field, moves the bibliography field or saves through Word;
those steps below are the author's work in Word, or a pass-specific script where marked.

## Conversion procedure

**FILE** is pure file work. **GUI** needs Word or EndNote desktop, run by the author or by an
agent under the rules at the end. **COM** is Word automation without clicks, only through
`msw.py word-qa`, which checks a read-only copy and never saves. Each phase ends with a gate that
must pass before the next phase starts.

**Phase 0, baseline (FILE).** Start from the author's latest save; record its SHA-256; run
`census` and `refs placeholders`. The author decides whether earlier tracked revisions are
accepted into a new baseline. Keep paragraph positions in one convention (descendant paragraphs,
which count table cells) with the text at each position.
Gate: each intended replacement has a ledger row (paragraph, offsets, old text, ordered PMIDs,
keys for non-PMID sources); look-alikes such as `(n)` sample sizes are excluded.

**Phase 1, identify sources (FILE, network).** The identity loop in
[reference audit](reference-audit.md), with RIS files for non-PubMed sources.
Gate: each ledger key maps to one verified PMID or source record; exceptions are written down.

**Phase 2, library (GUI).** Create a new, uniquely named library. Add records by PMID through
Online Search (PubMed), one per distinct PMID, and confirm each is in the local library. Import
the RIS files. End corporate authors with a comma. Compare fields with PubMed; do not trust Find
Reference Updates when it calls a record current. Export the library as EndNote XML.
Gate (FILE): `refs library-map` on the export shows one record per PMID and one db-id, and lists
records without a PMID; then check by reading the export that every journal article has title,
authors, year, journal, volume, pages or article number, DOI and `accession-num` (`refs census`
audits pages, DOI and PMID once the records are in the manuscript, Phase 5).

**Phase 3, pilot (GUI, then FILE).** On a separate copy, insert one citation (Alt+2), update
with the project's style, confirm it in Edit & Manage Citations, save under a new name, wrap it
(Phase 6), save it in Word and audit it (Phase 8).
Gate: a live field that resolves; accepted view the native result, rejected view the placeholder.

**Phase 4, conversion input (FILE).** On a clean copy (no tracked changes), run
`refs temp-citations` with the verified map. It splices only the placeholder text inside its
`w:t`, checks paragraph text, fields and pictures afterwards, and refuses the whole file when the
copy has tracked changes, the map spans several libraries, or a placeholder is unmapped,
malformed, points to a duplicated record or is split across runs (retype it in one piece in
Word). `--skip-unmapped` leaves unmapped and malformed placeholders as text and lists them in
the ledger (exit 1 when an unmapped one was skipped); a split placeholder is still refused.
Placing one complete, balanced `EN.REFLIST` at the References heading (from the pilot) and
removing old manual lists and old bibliography fields as complete fields is manual file work in
a pass-specific script.
Gate: no `[PMID` text left; temporary citations match the ledger; fields balance; the
bibliography span ends right after the References section and holds no drawing, heading or
legend; picture hashes unchanged.

**Phase 5, native update (GUI).** Open the input with only the intended library open, set
Configure Bibliography > Layout (Line spacing, Space after) from the journal profile, run Update
Citations and Bibliography, answer every matching prompt by hand, and save a clean native
checkpoint under a new name.
Gate (FILE, `census` and `refs census`): group, occurrence and distinct-record counts equal the
ledger; one db-id; each group's PMIDs equal its ledger PMIDs; one live bibliography; `_ENREF`
bookmarks equal distinct references when links are on; no missing link target; figures intact.

**Phase 6, tracked wrapper (FILE).** CWYW updates with Track Changes on do not give usable
revisions, so build them from files: the baseline supplies the deleted side, the native
checkpoint the inserted side. Pair ledger rows with complete native fields in order and assert
matching PMIDs. Wrap each old placeholder in `w:del` and each complete native field (nested DATA
fields and links included) in `w:ins`; a deleted field uses `w:delText` and `w:delInstrText`.
Wrap bibliography paragraphs as whole-paragraph insertions, with the paragraph-mark marker first
in `w:pPr/w:rPr`. Remap legacy bookmark ids into a range that does not overlap EndNote's. One
author, one date, fresh revision ids. `msw.py build` does not insert native fields, so this is a
manual step: a pass-specific script in the pass folder, gated by `msw.py verify` with a manifest
the script writes: `expected_accept` with one entry per changed paragraph (`index`, its position
among the source's descendant paragraphs; `new_index`, its position in the output, equal to
`index` when no paragraph is inserted; `text`, its accepted text; `deleted`, false; an inserted
paragraph, such as a new bibliography paragraph, has `inserted_after`, the source index it
follows, and `new_index` instead), and `declared` `citations` and `fields` set to true. The gate
compares paragraphs by position and
fails a manifest without `new_index`. Without the declaration the gate's `citations` and
`new_revisions` checks fail, by design.
Gate: accepted text equals the native checkpoint; rejected text equals the baseline; drawings
unchanged; no temporary citation in the accepted text; bookmark ids and names unique and every
`bookmarkStart` paired; no formatting revisions.

**Phase 7, Word round trip (GUI).** Open the tracked file, confirm Edit & Manage Citations
knows every entry, keep Instant Formatting off, Save As a new name, close it. `word-qa` cannot do
this step because it never saves.

**Phase 8, audit the saved file (FILE, COM).** Re-run the Phase 6 gates on the saved file
(`msw.py verify --allow-part-changes`, `census`), counting both link forms and checking every
target, `_ENREF_1` included. Run `msw.py word-qa` for Word's own accept and reject, and
`msw.py proof` for the accepted view (LibreOffice does not refresh EndNote fields). A Word PDF of
the final view needs EndNote fields locked so printing cannot rewrite them; the author makes it.
Look at the changed pages.

**Phase 9, hand-off.** Report each check's result, including the reference-list spacing against
`formatting.references_line_spacing` (`msw.py lint`'s `format.reference-spacing`). Deliver the
.docx, the `.enl` with its `.Data` folder beside it and the PDF proof. Mark superseded files "do
not use"; never delete them.

## Failure modes

| # | Failure | Symptom | Detect (file side) | Prevent |
| --- | --- | --- | --- | --- |
| F1 | Bibliography boundary | Figures after the References vanish on formatting | Bibliography span; drawings inside it; picture count | Carry complete balanced fields |
| F2 | Bookmark id reuse | After a save `_ENREF_1` is gone and the first link is dead | Unique ids including deleted content; link targets after the save | Remap legacy ids; pair starts and ends |
| F3 | Missing locators | Entries lack pages or article numbers | Journal articles with empty `pages`; PubMed `MedlinePgn`/`ELocationID` | Field-level PubMed comparison after import |
| F4 | Corporate author | An organisation printed as surname plus initials | Organisation-like authors without a trailing comma | Trailing comma in EndNote |
| F5 | Record numbers per library | Wrong paper or a matching prompt | One db-id; payload PMIDs equal the ledger | One library per manuscript; never merge; map from a fresh export |
| F6 | Record-number collisions | Claim checks read the wrong abstract | Distinct titles per record number | Key by `(db-id, rec)` |
| F7 | Multi-chunk payload | Parse errors or silently missing records | Parse every field; `<Cite>` counts against displayed numbers | Decode per chunk |
| F8 | Paragraph index convention | Ledger rows hit the wrong paragraph | Text at each index equals the ledger's old text | One convention, translated explicitly |
| F9 | Track Changes with CWYW | No usable revisions after a native update | Revision count after the update | Native checkpoint, then file-built wrapper |
| F10 | Instant Formatting | Tracked bibliography rewritten | `EN.InstantFormat` `Enabled`; revision structure diff | Keep it off; reformat only clean checkpoints |
| F11 | Link form change | Links found before the save, none after | Count `w:hyperlink` anchors and `HYPERLINK \l` fields | Audit after the save |
| F12 | Settings order | Unreadable-content warnings | Schema order of `w:settings` and `w:rPr` children | Insert at the schema position |
| F13 | File lock | Stale or failed reads | `~$` lock file beside the .docx | Close in Word first |
| F14 | GUI focus | Keystrokes land in the wrong control | Screenshot after every action | One grounded action per observation |
| F15 | Dead markers | Plain `(n)` numbers, no fields | `census`: no fields but numeric markers | Relink (below) |
| F16 | Same author and year | Wrong join on author-year keys | Duplicate `(author, year)` keys | Disambiguate by title or DOI |

## Relinking dead markers

When a version has lost its fields (plain `(1)`, `(14,15)`, `(16-18)` markers and a dead list):

1. Accept all revisions on a copy; check that the accepted text equals the plain text view.
2. Recover records from the latest earlier version with live fields (all three payload forms):
   `(db-id, rec)`, first author, year, title, journal, DOI.
3. Match each dead entry to a record: the normalised record title (25 characters or more) occurs
   in the entry and surname or year agrees; break ties deterministically. Each entry ends as
   MATCH, AMBIGUOUS or NO_RECORD.
4. Rewrite a marker as a temporary citation only when every number in it resolves; log the rest.
5. Leave the stale bibliography and dead list for the author to delete in Word before formatting.
   Hand over the references to import and the markers to cite by hand. Open all source libraries
   together without merging them. Formatting renumbers by first appearance and drops uncited
   entries.

When records cannot be recovered, identify by PMID and build a fresh library (Phases 1 and 2).

## Numbered fallback without EndNote

Last resort, when EndNote is unavailable and the journal accepts a plain numbered list: number by
first citation (main text, then legends); write groups as `(n, m)` and runs of three or more as
`n–p`; rebuild the list from PubMed records in the journal's style (for example at least three
authors before "et al."); unlink remaining fields by keeping their result; remove the orphan
bibliography field; recount words. Keep tabs, breaks and symbols in edited runs. Record the
decision: the list can no longer be refreshed.

## If an agent drives Word or EndNote

Only at the author's explicit request, never for routine placeholder conversion.

- Use the host's current computer-use skill. One grounded action per observation: verify focus
  before typing, reacquire dialogs instead of reusing coordinates, and do not treat a shortcut
  press as proof that a dialog opened.
- If the desktop is locked, stop and ask the author to unlock it, then start from a fresh
  screenshot. Read files only after Word has closed them.
- Word automation without the GUI goes through `msw.py word-qa` only.
