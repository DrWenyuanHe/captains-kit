# Revision: making version N+1

A revision is a new file built from one source version by `msw.py build`. Every change is a Word
tracked change or comment under the configured author, and the gate checks the result before it
is written. Run `msw.py` from the project folder; `PASS` below stands for the pass folder.

## 1. Choose the source

1. Run `msw.py status`. It reports the current version, whether its file hash changed since
   release (the author saved in Word), Word lock files and passes in flight.
2. Build on the newest file the author touched. If the author saved, accepted or replied in Word,
   register that save first with `msw.py author-save "<file>.docx"`; it becomes the current file
   and the source of the next pass. A file kept only for reference goes in with
   `msw.py intake "<file>" --role incoming` ([bootstrap](bootstrap.md#4-copy-sources-in-read-only)).
   A tracked copy returned by a supervisor or co-author that the next pass builds on goes in with
   `msw.py intake "<file>" --role author_saved --as-version <the version you sent> --state
   "<name> comments" --note "returned by <name>" --set-current`: the state keeps the sender in the
   file name and no version number is used up.
3. Do not build from a file that is open in Word or LibreOffice. `build` warns when the file's
   lock file (`~$<name>` or `.~lock.<name>#`) sits beside the source; ask the author to close the
   file first.
4. A Word save renumbers revision ids, comment ids and media names, and can change paraIds.
   After any save, extract again and list the comments again: paraIds, `source_hash` values and
   comment ids from before the save are stale.

## 2. Claim the pass

`msw.py pass new --slug "methods wording"` claims the next version number: it creates
`90_Agent_Work/passes/_claims/VNN.json` and `90_Agent_Work/passes/VNN_<slug>/` exclusively and
appends a `claim` event to the ledger, so two sessions cannot take the same number. It refuses
when the current file or its package copy changed since it was registered (run `author-save`
first) and while the indexes disagree about the current file (run the interrupted command again;
see [release](release.md#if-it-stops-part-way)). The folder holds `pass.json` (source version, path and SHA-256, revision author, build
file name `VNN tracked.docx`, `figures_changed`), an empty `edits.json` to fill in, a
`VNN_CHANGES.md` template, and `build01/`, `qa/`, `proofs/` and `review/`. It prints the `build`
(`--out "PASS/build01/VNN tracked.docx"`), `proof` (`--out "PASS/proofs/p1"`) and `release plan`
lines for the pass; the release gives the copy in `Versions/VNN/` its full name. A second build
attempt goes to `build02/`; `build` creates the `--out` folder when it is missing and, before it
writes anything, refuses an existing output file or manifest (with `--write-failed`, also an
existing `... GATE FAILED` file or its manifest). Nothing is overwritten.

## 3. Write edits.json

`msw.py extract SOURCE.docx --out DIR` lists every paragraph with its paraId, style, text and
`source_hash` (`DIR/paragraphs.jsonl`). Each edit names a paragraph with `para`:

- `"10000003"`: the `w14:paraId` (preferred; `{"paraId": "10000003"}` also works);
- `{"index": 9}`: the 0-based position that `extract` reports (also a bare integer, or a digit
  string under 8 characters);
- `{"contains": "unique text"}`: the one paragraph whose visible text contains it.

`before`, `old` and `after` are matched against the paragraph's visible accepted text. A
citation appears there as its displayed result, such as `(1)`, and formatting markup is not part
of it. `before + old + after` must occur exactly once in the paragraph. `op` defaults to
`replace`. Give each edit an `id` and a `purpose`; both go into the build manifest and the
change note.

| Op | Keys | Result |
| --- | --- | --- |
| `replace` | `before`, `old`, `new`, `after` | Minimal deletion and insertion |
| `insert` | `before`, `new`, `after` | Insertion at the point after `before` |
| `delete` | `before`, `old`, `after` | Deletion |
| `rewrite` | `text`, `source_hash`, `apply_formatting`, `allow_reattach`, `exact_spaces` | Whole paragraph diffed against its current text |
| `format` | `before`, `text`, `after`, `set`, `unset`, `highlight`, `rpr_add` | Tracked formatting change, text unchanged |
| `paragraph_format` | `ppr_add`, `ppr_remove` | Tracked paragraph-property change |
| `comment` | `before`, `anchor`, `after`, `text`, `author`, `initials` | New comment anchored to the phrase |
| `resolve_comment` | `comment` or `comment_text`, `mode` | Removes a thread or marks it done (section 8) |
| `insert_paragraph` | `after` (paragraph key), `text`, `style`, `like` | New tracked paragraph |
| `delete_paragraph` | `allow_fields` | Deletes the paragraph and its mark |
| `swap_picture` | `old_name`, `new_name`, `image`, `media`, `descr`, `width_mm` (or `width_emu`) | Tracked picture replacement |

A synthetic spec. Built on the test fixture (plus the paragraph `2000000A` and one comment), it
passes the gate with two warnings: `formatting` for the italics of e4 and `science_guard` for
the symbol and PMID in the paragraph e7 inserts; section 7 and [verification](verification.md)
explain them:

```json
{
  "edits": [
    {"id": "e1", "op": "replace", "para": "10000003", "before": "and ", "old": "fell later",
     "new": "fell afterwards", "after": ".", "purpose": "grammar: word choice"},
    {"id": "e2", "op": "insert", "para": "10000003", "before": "between groups", "new": ";",
     "after": " GENE1", "purpose": "grammar: run-on"},
    {"id": "e3", "op": "delete", "para": "1000000A", "before": "error bars show ", "old": "the ",
     "after": "SEM", "purpose": "house style"},
    {"id": "e4", "op": "format", "para": "2000000A", "before": "incubated ", "text": "in vitro",
     "set": ["italic"], "purpose": "italics: Latin phrase"},
    {"id": "e5", "op": "paragraph_format", "para": "1000000A", "ppr_add": "<w:keepNext/>",
     "purpose": "keep legend with its figure"},
    {"id": "e6", "op": "comment", "para": "10000003", "anchor": "expression rose after training",
     "text": "[Logic] Please confirm the direction against Figure 2B.", "purpose": "logic"},
    {"id": "e7", "op": "insert_paragraph", "after": "10000002",
     "text": "A new sentence about ⟨i⟩GENE1⟨/i⟩ in this cohort [PMID: 12345678].",
     "purpose": "author-approved addition"},
    {"id": "e8", "op": "resolve_comment", "comment_text": "cohort mean",
     "purpose": "author answered in V07; addressed by e1"},
    {"id": "e9", "op": "swap_picture", "old_name": "Figure 1", "new_name": "Figure 1 v2",
     "image": "../../Figure_QA/Figure_01/v2_embed300/Figure_01_v2_embed300.png",
     "media": "media/v08_figure_01_v2.png", "descr": "Figure 1, revised", "width_mm": 160,
     "purpose": "revised figure"}
  ]
}
```

Notes on the ops:

- `new` and `text` may carry formatting markup: `⟨i⟩…⟨/i⟩`, `⟨b⟩`, `⟨sup⟩`, `⟨sub⟩`, `⟨u⟩`. In
  inserted text, a tab, `\n` and `\f` become a Word tab, line break and page break; U+2011 becomes
  a non-breaking hyphen.
- `replace` compares text only. A change that only adds formatting (`H2O` to H₂O) is a `format`
  op; `replace` refuses it as "the same text".
- `format` `unset` also turns off a flag that comes from the paragraph or character style (an
  Emphasis style's italics, say): the run gets an explicit "off" value, tracked like any
  formatting change. Superscript and subscript replace each other in one op.
- `format` `set` and `unset` take `bold`, `italic`, `underline`, `strike`, `caps`, `smallcaps`,
  `superscript`, `subscript` and `highlight`. A highlight takes its colour from `"highlight"`
  (default `yellow`; also `green`, `cyan`, `magenta`, `blue`, `red`, `black`, `lightGray`,
  `darkGray` and the `dark` forms `darkBlue`, `darkCyan`, `darkGreen`, `darkMagenta`, `darkRed`,
  `darkYellow`). Any other flag name or colour is refused. Use `rpr_add` for other run
  properties, such as `"<w:noProof/>"`; it replaces any property with the same tag.
- `rewrite` gives the whole new paragraph text with every atom in place (section 4). With
  `"apply_formatting": true`, markup differences on unchanged words also become tracked
  formatting changes; otherwise only inserted words take the markup.
- `comment` text may hold `\n` for several comment paragraphs. `author` and `initials` default to
  the revision author and its initials.
- `insert_paragraph` places the new paragraph after `after`. Its paragraph properties come from
  the anchor, except after a heading followed by body text, where they come from that following
  paragraph. `"like": <paragraph key>` copies another paragraph's properties instead, and
  `"style": "BodyText"` sets the style id on top (an id defined in the document's styles; the
  engine does not check it). The runs take the anchor's first text run properties, with the
  markup setting the flags. When the new properties differ from the anchor's, the build records
  the anchor's old properties as a tracked `pPrChange` and declares it in the manifest, so the
  gate does not warn about it. Several `insert_paragraph` edits with the same `after` go in spec
  order. New paragraphs cannot contain
  atoms: write citations as `[PMID: n]` placeholders for the author's EndNote conversion
  ([EndNote](endnote.md)).
- `delete_paragraph` refuses a paragraph with a field, citation or picture unless
  `"allow_fields": true`; the build then declares the removed citations (the gate's citation
  check becomes a warning) and pictures (`declared.pictures_removed`, so `pictures` passes). It
  also refuses a paragraph that ends a section (it carries a section break, for example before a
  landscape page) and the last paragraph before a table, in a table cell, in a text box or in the
  document, which has no following paragraph to merge into: delete their text instead. An empty
  paragraph that Word saved as a self-closing `<w:p/>` can be deleted, used as an
  `insert_paragraph` anchor and given a `paragraph_format` like any other.
- `swap_picture` finds the visible picture by its drawing name (or give `para`). A relative
  `image` path resolves against the folder of the spec file, not the working folder (above: from
  the pass folder up to `90_Agent_Work/`; `../../../` reaches the project root). An absolute path
  also works, and is the safe choice on Windows when the project root is long. PNG, JPEG or GIF;
  the height follows the image's aspect ratio at `width_mm`
  (default: the old width). The new media part is `word/media/msw_imageN.<ext>` unless `media`
  names a new one (a name that matches an existing part in any case is refused); the old part
  stays in the package because the deleted run still points to it.
  See [figures](figures.md).
- The top-level `intended` list, `[{"old": "...", "new": "..."}]`, states whole-text replacements
  in the accepted text. The gate applies them in order to the source's accepted text (paragraphs
  joined by `\n`; each `old` must occur exactly once) and fails unless the result is exactly the
  candidate's accepted text. This runs in addition to the per-paragraph expected text the
  manifest always carries, so the list must cover every text change in the build. Use it for a
  small, high-stakes build, where it proves nothing but the stated wording changed.

## 4. Rewriting many paragraphs

For grammar, humanizer or polish passes over many paragraphs, let agents rewrite plain text and
let the engine compute the tracked changes:

```
msw.py extract SOURCE.docx --out PASS/qa/rewrite01 --min-chars 20 --exclude-style Heading EndNoteBibliography
  (agents read PASS/qa/rewrite01/in/<key>.txt and write PASS/qa/rewrite01/out/<key>.txt)
msw.py collect PASS/qa/rewrite01 --out PASS/qa/rewrite01/edits.json --purpose "grammar: small fixes"
  (copy its "edits" into PASS/edits.json, next to any other ops of the pass)
msw.py build SOURCE.docx PASS/edits.json --out "PASS/build01/V08 tracked.docx" --id-base 180000
```

`extract` wants a new or empty `--out` folder; `--exclude-style` skips styles by prefix,
`--min-chars` (default 1) skips paragraphs with fewer editable characters, and `--paragraphs`
limits it to named paraIds or indexes. `<key>` is the paraId when it is eight hexadecimal digits
and unique, otherwise `P` and the 0-based index (`P0007`). `collect` strips a byte-order mark and
CRLF and one final newline an editor added (a paragraph that really ends in a line break keeps
it), skips output files equal to their input, and writes one `rewrite` op per changed paragraph
with the `source_hash` from extraction. Like every
command it refuses an existing output, and `pass new` has already created `PASS/edits.json`, so
collect into the rewrite folder. Each `in/` file looks like:

```
Marker levels were compared between groups ⟨i⟩GENE1⟨/i⟩ expression rose after training ⟦F1⟧ and fell later.
```

Atoms stand for content that is never edited as text: `⟦F1⟧` a field (EndNote citation,
bibliography, cross-reference, any other field), `⟦D1⟧` a picture or embedded object, `⟦X1⟧`
anything else that is not plain text (a hyperlink outside a field, a content control, an
equation, a footnote reference). Numbering restarts per kind in each paragraph.

Give each rewriting agent these rules:

1. Keep every atom, byte for byte, in the same order, attached to the same words and punctuation.
2. Keep the formatting markup on text you do not change. New words without markup come out
   plain, so mark a new italic gene symbol as `⟨i⟩GENE1⟨/i⟩`.
3. Keep numbers, units, statistics, gene and protein symbols, and the direction and strength of
   every claim. Anything that needs one of these changed is a note back to you, not a rewrite.
4. Keep the spelling locale and the author's sentence structure; change as few words as the fix
   needs. Output only the paragraph: no quotes, notes or questions.
5. To leave a paragraph unchanged, copy it or write nothing: `collect` treats an empty output
   file as unchanged, and `build` refuses a `rewrite` whose text is empty. Deleting a paragraph is a
   `delete_paragraph` op.

## 5. What the engine refuses

| Refusal | Why | What to do |
| --- | --- | --- |
| "the edit falls inside ⟦F1⟧" | Citation text, field results and every bibliography paragraph are atoms; EndNote rewrites them on refresh | Edit the words around the field; fix references in EndNote |
| "the rewrite moves '…' across a citation" | Text moved to the other side of an atom re-attaches the citation to a different claim | Keep the text on its side, or check that the citation supports the new clause and set `"allow_reattach": true` |
| "atoms changed: [...] -> [...]" or "would change ⟦F1⟧" | A rewrite or deletion dropped, duplicated or reordered an atom | Restore the atoms. The engine never removes a citation inside a paragraph; comment and let the author remove it in Word |
| "the paragraph changed since it was extracted (stale rewrite)" | `source_hash` differs from the paragraph now in the source | Extract again from the current source and redo that paragraph |
| "overlapping edits (a and b)" | Two edits claim the same characters | Merge them into one edit |
| "context '…' found N times" | `before + old + after` is not unique in the paragraph | Add context |
| "cannot insert a paragraph inside the EN.REFLIST field" or "the paragraph lies inside the EN.REFLIST field" | Paragraph ops inside the bibliography; EndNote rebuilds it | Leave the bibliography to EndNote |
| "cannot insert after a paragraph that is being deleted" or "the anchor paragraph ends a section" | The anchor cannot carry the inserted mark | Anchor on another paragraph |
| "the paragraph ends a section" (`delete_paragraph`) | Deleting its mark would remove the section break and merge two sections (a landscape page, say) | Delete its text instead, or change sections in Word |
| "the paragraph is the last one before a table, in a table cell, in a text box or in the document" | Word has no following paragraph to merge it into | Delete its text instead |
| "the paragraph contains fields or pictures; set allow_fields to delete them" | `delete_paragraph` would remove a citation, field or picture | Confirm the removal with the author, then set `"allow_fields": true` |
| "new paragraphs cannot contain atoms" | `insert_paragraph` text holds `⟦F1⟧` | Use `[PMID: n]` placeholders |
| "N comments contain '…' (need 1)" or "comment N not found" | `resolve_comment` did not name exactly one comment | Use the id from `msw.py comments` on the build's source, or longer `comment_text` |
| "this document has no commentsExtended.xml; resolve by removing the thread instead" | `"mode": "done"` on a file Word has not saved with comments | Use `"mode": "delete"` |
| "unsupported formatting [...]" or "highlight colour must be one of [...]" | A `format` flag or highlight colour the engine does not write | Use a listed flag or colour, or `rpr_add` |
| "contains earlier tracked changes" | Deleting an atom that holds someone's pending revision | Ask the author to accept or reject those changes first |
| "id base N is below the largest existing w:id M" | New ids would collide with existing ones | Leave out `--id-base`, or give one at least M |
| "the source has broken fields" | Unbalanced field markup in the source | Stop; report it to the author before editing |
| no revision author | `--author`, `MSW_AUTHOR` and `author.revision_name` are all empty | Set `revision_name` in `manuscript.json` |

A refused spec writes nothing. A spec that builds but fails the gate writes nothing either, unless
`--write-failed` is given for inspection (the file is then named `... GATE FAILED.docx`). The exit
code is 0 on PASS, 1 on a gate failure and 2 on a refusal.

## 6. How the changes are written

**Minimal diff.** The engine diffs word tokens between atoms. Adjacent replacements separated by
one short space are joined, and an unchanged stretch no longer than the edits on both sides is
absorbed. The result: a one-word fix stays one word, and a rewritten clause reads as one deletion
and one insertion instead of a scatter of fragments. The gate's `minimality` check warns about
any replacement that still shares its first or last word.

**Special spaces.** A `rewrite` may add a no-break space (also a narrow no-break space or a
figure space) where the text has a plain one, and that becomes a tracked change. The other
direction is ignored: where the source has a special space and the rewrite has a plain one, the
special space stays and no change is written, so an agent that normalizes special spaces creates
no whitespace noise. To turn a special space into a plain one, give the rewrite
`"exact_spaces": true` (every space character then counts as itself) or use a `replace` of the
space itself. A `replace` always writes exactly the characters given, for example
`{"op": "replace", "para": {"contains": "see Figure 2"}, "before": "Figure", "old": " ", "new": "\u00a0", "after": "2"}`.

**Formatting of inserted text.** In a `rewrite`, and in a `replace` whose `new` contains any markup
tag, the markup is authoritative and unmarked inserted characters are plain. Without markup, a
`replace` or `insert` copies the formatting of the text it replaces if that text is uniform;
otherwise it takes a flag only when both neighbours carry it. So text typed after an italic gene
symbol is not italic. A replaced span that mixes formatting keeps its shared start and end, so
`kg/m2` to `kg/cm2` keeps the superscript 2. Font, size and other run properties come from the
neighbouring run, but never a symbol or dingbat font. Text in the Symbol font (or a Symbol
`w:sym` character) is read as the Unicode character it shows, so `before`, `old` and rewrites see
μ or ±; inserted text is written as Unicode and copies the nearest run in a text font (without a
font of its own when there is none). Characters the edit does
not touch keep their formatting; the gate fails a build that loses formatting on unchanged text.
An edit may end right before a comment reference or another zero-width mark.

**Nested edits.** Text inside an earlier, unaccepted insertion (a co-author's or an earlier pass's)
can be edited. The engine writes Word's own form: the original insertion keeps its author and
date, your deletion sits inside a continuation of it, and your insertion follows. The gate accepts
continuation wrappers that carry the earlier author.

**Author and date.** The author comes from `--author`, then `MSW_AUTHOR`, then
`author.revision_name` in `manuscript.json`, the name chosen at bootstrap (the author asked for
their own). Use the overrides only when the author asks; never fall back to a model name. One
ISO-8601 UTC date covers the whole build (default: now; `--date` sets it).

**Revision ids.** Projects give each version its own range so change notes can cite ids: by
convention `--id-base` is `(N + 10) x 10000` for version N ([workspace](workspace.md#names)); the
line `pass new` prints leaves it out, so add it. Ids start at base + 1. Without `--id-base`, the
base is the next multiple of 10,000 above the largest `w:id` in any story part of the source, so
it adapts after a Word save. A base below an existing id is refused; `msw.py inspect` shows the
document's `max_w_id`. The manifest records the range used.

**Other build options.** `--track-revisions on|off|keep` sets Word's Track Changes switch in the
output (default `keep`); `on` also turns on a switch the source has set to off. `--manifest` names the manifest file (default `<output stem>.manifest.json`).

## 7. Edit or comment

Triage each finding before it enters `edits.json`:

| Class | Examples | Goes in as |
| --- | --- | --- |
| Writing | grammar, spelling, punctuation, word choice, house style | Minimal tracked edit, no comment |
| Science or data | a claim, a number, a statistical choice, an effect direction, a figure mismatch | Comment for the author |
| Reference | wrong or missing citation, bibliography problem | Comment only when asked; see [reference audit](reference-audit.md) |
| Author decision | two defensible options | Numbered options with a recommendation in the report |

`policy.science_changes` in `manuscript.json` sets the Science row: `comments` (the default) or
`tracked_for_review`, where science changes the author asked to have addressed go in as tracked
edits for the author's review ([bootstrap](bootstrap.md#1-ask-the-questionnaire)); `msw.py status`
prints the setting. Either way, no edit may change a number, a statistics term, a gene or protein
symbol, or the direction or strength of a claim unless the author approved it or it was
recomputed from the data; note the approval in `purpose`. The gate backs this up with two warnings on every build:

- `science_guard` lists each changed, inserted or deleted paragraph whose accepted text gains or
  loses a number, a statistics term (P, q, n, CI, OR, HR, RR, SD, SEM, AUC, AUROC), an
  all-capitals symbol of three or more characters (gene-like symbols and acronyms) or a direction
  word (increase, decrease, higher, lower, greater, smaller, rose, fell, decline, reduce, elevate,
  positive, negative, more, less and their forms).
- `citation_context` lists each citation whose six preceding words changed, so it may now support
  different text. It compares words only, so a punctuation fix just before a citation does not
  trigger it.

Both are WARN: look at every item and confirm it against an approval or the data. They do not see
hedges and negations (`not`, `may`, `suggests`) or lower-case symbols. `science_guard` compares
runs of neighbouring changed paragraphs as one group, so a paragraph inserted next to an edited
one is reported with it (`paragraphs 3-4: ...`), and a token that only moved between them is not
listed. The reviewer still reads every edit and new paragraph for numbers and claim strength. A
build with an unapproved item is not presented; fix the spec and build again into the next
`buildNN/` folder.

Comments are anchored to the sentence or phrase they concern, never a whole paragraph. Start each
with the pass tag (`[Logic]`, `[Nomenclature]`) because every comment carries the author's name.

## 8. The author's replies

The author answers in Word comment threads. To act on them:

1. List the threads in the author's save: `msw.py comments SAVED.docx` prints each thread with
   its id, author, date, the anchored text (`on:`), the text, indented replies and `[done]` for
   threads Word marks resolved; `--json` gives the same as data (`id`, `author`, `initials`,
   `date`, `text`, `anchor`, `done`, `replies`). It reads the file only.
2. Register that save (`author-save`) and build from it, so the ids you see are the ids of the
   build's source. Across saves, match threads by anchor and text, never by id: Word renumbers
   comment ids on save.
3. Turn each instruction into ops in the next spec. Where the author asks for new text, compose it
   as a tracked insertion. Where a reply closes a whole category ("no more parameter comments"),
   record it in `06_Project_Docs/DECISIONS.md` so later passes do not raise it again.
4. Close each addressed thread in the same spec with `resolve_comment`, one op per thread:
   `"comment": "<id>"` or `"comment_text": "<text found in exactly one comment or reply>"`.
   `"mode": "delete"` (the default) removes the named comment, every reply below it and their
   range markers; naming the first comment removes the whole thread. `"mode": "done"` marks it
   resolved as Word's Resolve does; it needs `word/commentsExtended.xml`, which Word writes when
   it saves a file with comments. On a file only the engine has written, `done` is refused
   ("this document has no commentsExtended.xml; resolve by removing the thread instead", exit 2),
   so use `delete` there; any other mode is refused too. Word does not track comment removal: the
   manifest lists removed ids under `declared.comments_removed` and one `resolve_comment` record
   per edit.

   Replies exist only when Word writes them. The engine's `comment` op always starts a new
   top-level thread and cannot reply to one, so an agent's answer to the author is a new comment
   on the same anchor, and closing it later takes its own `resolve_comment`. A reply thread
   cannot be rehearsed without Word.
5. Name the resolved threads in `VNN_CHANGES.md` and in the report, with what was done for each.
   Leave unaddressed threads open and say why.

## 9. After the author accepts in Word

When the author accepts or rejects changes and saves:

1. Register the save with `msw.py author-save "<file>.docx"`; it becomes the source of the next
   pass. Its state is `author accepted` when no tracked changes remain, else `author saved`
   (`--state accepted|saved` overrides).
2. See what the author changed: `msw.py verify RELEASED.docx SAVED.docx --allow-part-changes`.
   Without a manifest, `accepted_text` lists the paragraphs whose accepted text differs (INFO: the
   author's own edits and rejected changes), `untracked_changes` fails with every difference in
   the rejected view (accepted changes included), and `science_guard` names changed numbers or
   directions. Here those lines describe the author's work; they are not failures to fix.
3. Extract again and list the comments again. Replace ops located by text context still apply
   if the text is unchanged; rewrite ops for changed paragraphs refuse as stale, which is the
   intent.
4. Build as usual. Word renumbered the revision ids on save, so confirm the version's
   `--id-base` is at least the new source's largest id (`msw.py inspect`); `build` refuses one
   below it.
