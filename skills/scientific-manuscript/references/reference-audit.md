# Reference audit

Use these loops to show that each reference exists, is the paper the text means, carries
complete metadata and supports the sentence that cites it. Each loop writes new files and ends in
a report for the author.

Fixes never go into EndNote fields or the bibliography. A wrong or incomplete reference becomes a
Word comment at the citation, or a row in the hand-off list the author works through in EndNote.
A sentence that misstates its source is a science question: comment with a proposed wording
unless the author has approved the change. See [EndNote](endnote.md) for the field rules.

## Choose the loop

| Question | Loop | Commands |
| --- | --- | --- |
| Is `[PMID: n]` the paper the text means? | Identity | `refs placeholders`, `refs pubmed` |
| Does each bibliography entry resolve, with the right title and year? | Metadata | `refs bib-audit`, `refs crossref` |
| Will EndNote print complete records? | Payload metadata | `refs census` |
| Does each cited paper support its sentence? | Claim support | `refs claims`, `refs claims-report` |
| What should the author change? | Replacement table | from the claim review |
| Did the author's refresh fix what was flagged? | Follow-through | `refs bib-audit` again (cached) |
| Which EndNote record holds this PMID? | Library map | `refs library-map` |

All are `msw.py refs <command>`; `--help` lists the flags. Commands that take `--out` refuse to
replace any file they would write; to keep what a command only prints, redirect it into a new
file ([bootstrap](bootstrap.md#6-register-v01)). Each exits 0 when clean, 1 when it found
something to act on (listed per loop below) and 2 on a refusal or unusable input.

## Network and evidence

- Only PubMed E-utilities and Crossref are contacted, over HTTPS. A redirect is followed only to
  an https URL on those hosts; any other is refused as a fetch error. Set `MSW_CONTACT_EMAIL`
  (sent as the E-utilities `email` and in the user agent) and, if available, `NCBI_API_KEY` (a
  higher NCBI rate); neither is ever sent to a host other than the one asked. Never write an
  address into a script.
- Responses are cached in `--cache`, else `MSW_HTTP_CACHE`, else the project's
  `05_References/Evidence/http_cache`, else `OUT/http_cache`; reruns fetch only what is new.
  `--no-cache` bypasses it. Transient errors are retried with backoff (`--max-retries`, default 4).
- `refs pubmed [PMID ...] [--from-docx DOCX] --out DIR` fetches in batches of up to 200
  (`--batch-size`) and writes `DIR/records.json` and `DIR/evidence/`: each raw XML response, its
  `.sha256` and a `.request.json`. Each record points to its evidence file, hash and XPath. It
  exits 1 when a PMID was not found or a fetch failed. `refs crossref [DOI ...] [--from-docx
  DOCX] --out DIR` does the same for DOIs into `DIR/crossref.json`.
- Put evidence runs under `05_References/Evidence/<date>_<purpose>/`, and audit runs in a new
  dated folder under `90_Agent_Work/QA/` or in the pass's `qa/` folder.
- Status per PMID is `ok`, `no_abstract`, `not_found` or `fetch_error`; per DOI `ok`, `not_found`
  or `fetch_error`. A fetch error is never reported as "no abstract" or "not found".

## 1. Identity: PMID mapping with archived evidence

1. `refs placeholders <docx> --json <new file>` lists every `[PMID: n]`, `[PMIDs: ...]`,
   `[REF: key]` and temporary citation with its paragraph (exit 1 on a malformed one).
2. `refs pubmed --from-docx <docx> --out <new folder>` fetches the PMIDs in the placeholders and
   citation payloads.
3. Compare each record with the reference the text intends: exact title, first author surname
   and initials (or collective name), journal, year, and a DOI in the same record. An online-first
   year that differs from the issue year is a note, not a mismatch.
4. Write one row per reference into an identity file in the run folder (a manual step; no command
   writes it): `pmid`, `status`, the in-text locations, the record's fields, its evidence pointer,
   `checks {title_exact, author_match, journal_match, year_match, doi_in_record}`,
   `match_rationale` and `notes`.
5. For a source without a PMID (dataset, website, report), save the official page, record the
   exact-title search that found nothing, and prepare a RIS file for the author to import
   (`TY  - DATA` for a dataset, corporate `AU` ending in a comma, an `N1` provenance note).

Gate: every placeholder maps to one verified record or a written exception, and the number of
distinct PMIDs equals the number expected. Use a browser only for the ambiguous rows. A verified
mapping is written once; a rerun writes a new file and is compared with it.

## 2. Metadata: rendered bibliography against the records

`refs bib-audit <docx> --out <new folder> --records <records.json> [--crossref] [--style NAME]`
reads the bibliography (EndNote Bibliography paragraphs or the `EN.REFLIST` span) and writes
`bib_audit.json` and `BIB_AUDIT.md`. `--records` takes `records.json` or `crossref.json` and may be
repeated; `--crossref` also looks up, with the cache, entry DOIs no record covers. An entry
without a DOI is matched to a record by title when at least 90% of the record's title words
appear in it. The year pattern follows the style in the document's EndNote settings, or
`--style`. It reports gaps, duplicates and order in the numbering and, per entry:

| Issue | Meaning |
| --- | --- |
| `NO_DOI` | No DOI in the entry; check it through its PubMed record instead. |
| `DUPLICATE_DOI` | The same DOI appears under two numbers: a duplicate reference. |
| `NO_RECORD` | No PubMed or Crossref record to compare with. |
| `FETCH_ERROR` | The Crossref lookup failed (network or HTTP error), so the entry was not compared; the error is printed and kept in the audit. |
| `TITLE_MISMATCH` | Fewer than half of the record's title words appear in the entry. |
| `YEAR_NOT_FOUND` | No year could be read in the entry for this style (`--style`). |
| `YEAR_MISMATCH` | The years differ by more than one (print versus online-first allowed). |

It exits 1 on `TITLE_MISMATCH`, `YEAR_MISMATCH`, `DUPLICATE_DOI`, `FETCH_ERROR` or numbering that
is not contiguous, and 2 when every Crossref lookup failed, so nothing was compared: rerun with a
new `--out` when the network is back. A Crossref `not_found` for a DataCite DOI (Zenodo, figshare) is not a dead DOI; check
it at doi.org. Report the verified count, every flagged entry, duplicates, and cosmetic issues
such as a publisher item identifier stored where the DOI belongs.

## 3. Payload metadata audit

The payloads in the manuscript are exactly what EndNote will print, so they can be audited
without EndNote. Run `refs census <docx>` on the author's latest save or a native checkpoint. On
top of the citation census it reports:

- journal articles without pages or an article number (EndNote's Find Reference Updates has
  called such records current);
- missing DOI and missing PMID (`accession-num`);
- organisation-like authors without a trailing comma, which print as surname plus initials;
- first-author and year pairs shared by different records;
- record numbers used by more than one library, duplicate PMIDs or DOIs, and the number of
  library db-ids.

It exits 1 on field errors, missing link targets or unreadable payloads. The field-level
comparison is manual: compare each flagged record with its archived PubMed record from
`refs pubmed` (`MedlinePgn` or `ELocationID`, volume, issue, title, author count), and check that
each citation group's PMIDs equal the verified identity mapping. Each finding becomes a hand-off
row: record, field, the value PubMed holds.

## 4. Claim support

1. **Batch.** `refs claims <docx> --out <new folder> --records <records.json>` pairs each citation
   field with its claim sentence from the accepted view and the cited records' bibliography line,
   PMID and abstract (from `--records`, repeatable; without it there are no abstracts). Records
   are keyed `db-id#recnum`, so papers from different libraries stay apart. It writes
   `batches/batch_NN.json` (`--batch-size`, default 40 fields), an empty `findings/`,
   `VERDICT_SCHEMA.json` and `claims_manifest.json`, and exits 1 when a citation payload could
   not be read. Abstracts are complete unless `--max-abstract-chars` (at least 3000) is set, and
   any truncation is flagged.
2. **Judge.** Independent reviewers (subagents or another model) each take batch files and write
   the named `findings/batch_NN.json`:

   ```json
   {"batch": 3, "findings": [
     {"field_index": 41, "cited": ["abcd1234#12"], "verdict": "MISMATCH",
      "reason": "The abstract reports marker Q-17 in mice; the sentence is about people.",
      "suggested_action": "TEXT", "confidence": "medium", "evidence": "both",
      "notes": "full text checked: no human samples were studied"}
   ]}
   ```

   Required: `field_index`, `cited` (keys from the item), `verdict` (`SUPPORTED`, `WEAK`,
   `MISMATCH`, `UNVERIFIED_NO_ABSTRACT`), `reason` and `suggested_action` (`REUSE`, `ADD`,
   `TEXT`, `NONE`). Optional: `confidence` (`high`, `medium`, `low`), `tier` (1 to 3),
   `evidence` (`abstract`, `full_text` or `both`: what the verdict rests on), `replacement` (the
   reference to reuse or the PMID to add) and `notes`. No other keys are allowed. Give reviewers
   an even-handed brief: reviewers told to "default to refute" have dismissed real defects.
3. **Report.** `refs claims-report <folder> [--out REPORT.md]` validates every findings file
   against the schema and writes `CLAIM_AUDIT.md` (default) by tier. A `MISMATCH` goes to the tier
   its `tier` names; otherwise:
   - **Tier 3**: the right paper, but the sentence misstates it (`suggested_action` `TEXT`); the
     fix is wording;
   - **Tier 1**: clearly the wrong paper (confidence high), typically a mis-picked record;
   - **Tier 2**: probably wrong (any other confidence); confirm against the source the sentence
     was written from;
   - **WEAK** and **unverified** items, listed separately.

   Invalid findings, duplicates and unjudged fields are listed, and the command then exits 1.
4. **Curate.** Re-check every flag against its evidence yourself before it reaches the author, and
   record false positives with the reason.
5. **Escalate to full text.** Abstract-only verdicts over-flag: methods, secondary outcomes and
   population details are often missing from the abstract. Read the full text (PubMed Central or
   the publisher's open version) for every Tier 1 and Tier 2 flag and for WEAK and unverified
   items that matter. Set `evidence` to `full_text` or `both` and say in `notes` what the full text
   showed; `notes` is printed in the report, `evidence` stays in the findings file. When no full
   text is available, keep `evidence` `abstract` and label the row "abstract only".

Paper identity rests on DOI or PMID. Record numbers repeat across libraries, and display numbers
change whenever EndNote renumbers.

## 5. Replacement table

One row per flagged in-text citation, in document order:

| # | Location | Claim need | Fix | PMID | Confidence |
| --- | --- | --- | --- | --- | --- |
| 1 | 2.3, para 4 | Q-17 rise with training | REUSE #18 | 23456789 | high |
| 2 | 4.1, para 2 | Reference range in healthy adults | ADD PMID | 34567890 | medium |
| 3 | 4.2, para 1 | Direction of the effect | TEXT: "was associated with" | 12345678 | high |

- **REUSE #N**: an entry already in the bibliography fits; the author repoints the citation.
- **ADD PMID**: a new reference for the author to import and cite.
- **TEXT**: the reference is right and the sentence needs rewording.
- **Verify then cite**: dataset provenance where an accession may be mistyped.

Give a PMID for every row, reuses included, checked against PubMed. End with a quick-action
summary grouped by fix. The author applies REUSE and ADD rows in Word and EndNote. TEXT rows go
into the next version as comments with the proposed wording, or as tracked edits once the author
approves them.

## 6. Follow-through

After the author refreshes EndNote and saves:

1. Register the save as the new baseline (`msw.py status`, then `msw.py author-save`; see
   [release](release.md)).
2. Re-run `refs bib-audit` and `refs census`; the cache means only new DOIs and PMIDs are fetched.
3. Check that the reference changes map onto the earlier Tier 1 and Tier 2 rows, and that every
   newly added reference verifies.
4. List what still needs confirmation in the text.
5. Say what was not re-run (for example the full claim-support loop) and leave the final refresh
   in Word to the author.

## Report back

One line per loop that ran (checked, passed, flagged), then the replacement table or payload
findings, then numbered decisions with a recommended option. Name every loop that could not run
(no network, no library export) and why.

## Pitfalls

- Keying records by record number alone joins the wrong paper when several libraries were used.
- Joining base64 strings before decoding loses records in multi-chunk payloads.
- Reading display numbers from one superscript pattern misses `(n)` and `[n]` styles.
- Sentence splitting on every full stop breaks at "et al." and "e.g.".
- Ranges written with an en dash ("3–5") need parsing like hyphen ranges.
- `accession-num` is a PMID only for PubMed imports; confirm it against the record's source.
