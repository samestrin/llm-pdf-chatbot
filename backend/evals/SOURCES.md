# Eval fixture sources

The five PDFs in `pdfs/` that the eval questions are asked against. Each is freely licensed and
at most 5 MB. `tests/test_eval_fixtures.py` fails if a PDF in `pdfs/` is missing from the table,
its sha256 differs, it is over 5 MB, or its URL, licence or retrieval date is empty.

Each URL is the file the PDF was downloaded from, byte for byte; the Wikimedia Commons page for
each file (linked below) records its author and licence.

| File | Kind | URL | Licence | Retrieved | sha256 |
|---|---|---|---|---|---|
| contract-wmf-grant-agreement.pdf | contract | https://upload.wikimedia.org/wikipedia/commons/7/75/Sample_Grant_Agreement_for_nonprofits.pdf | CC BY-SA 4.0 | 2026-11-05 | 27688dadbf248d38491ff974c7bdfecb4dc7f6f617689e685cef0d035eeecffd |
| manual-usace-hazus-user-guide.pdf | manual | https://upload.wikimedia.org/wikipedia/commons/8/83/Ohio_HAZUS-MH-2023_user_guide_-_USACE-p16021coll11-6597.pdf | Public domain (work of the U.S. federal government) | 2026-11-05 | ac94405071f59d7000fcd4a26769e09cf23eaff42a1eaffd08df4c32a348c55c |
| report-cbo-work-in-2018.pdf | report | https://upload.wikimedia.org/wikipedia/commons/2/28/Congressional_Budget_Office_report_2019-03-28_-_The_Congressional_Budget_Office%27s_Work_in_2018_-_A_Report_to_the_Congress.pdf | Public domain (work of the U.S. federal government) | 2026-11-05 | a472538bbee88d7e21b7490a00a9c2c6281443ded629d8b82c9c829fa5f599c4 |
| scanned-lbj-desk-diary-1954-10-13.pdf | scanned | https://upload.wikimedia.org/wikipedia/commons/f/fe/Lyndon_Johnson%27s_1954_Desk_Diary_I%2C_10-13-1954_note_-_DPLA_-_00aeaf102c2df033a59aff9edc2dd2d3.pdf | Public domain (U.S. National Archives, LBJ Library) | 2026-11-05 | ae2760e8eb74b5f2dde8688672c03930c44be8ce7d642d3e6f5550cbee1498be |
| table-cbo-monthly-budget-review-2018-12.pdf | table | https://upload.wikimedia.org/wikipedia/commons/4/48/Congressional_Budget_Office_report_2019-01-08_-_Monthly_Budget_Review_for_December_2018.pdf | Public domain (work of the U.S. federal government) | 2026-11-05 | 36a1c7666f73dca1bffc77d362fe3999316e2e6c2973b32f1e49ae5f491a278b |

## Notes

- **contract**: Wikimedia Foundation, "Sample Grant Agreement for nonprofits", 9 pages, uploaded by
  Jtud (WMF). Commons page:
  https://commons.wikimedia.org/wiki/File:Sample_Grant_Agreement_for_nonprofits.pdf. CC BY-SA 4.0
  (https://creativecommons.org/licenses/by-sa/4.0): reuse needs attribution and the same licence
  for this file.
- **manual**: U.S. Army Corps of Engineers, Huntington District, "Ohio HAZUS-MH 2023 User Guide"
  (21 Sep 2023), 9 pages. Original: https://usace.contentdm.oclc.org/digital/collection/p16021coll11/id/6597.
  Commons page: https://commons.wikimedia.org/wiki/File:Ohio_HAZUS-MH-2023_user_guide_-_USACE-p16021coll11-6597.pdf
- **report**: Congressional Budget Office, "The Congressional Budget Office's Work in 2018: A Report
  to the Congress" (March 2019), 8 pages. Original: https://www.cbo.gov/publication/54991.
  Commons page: https://commons.wikimedia.org/wiki/File:Congressional_Budget_Office_report_2019-03-28_-_The_Congressional_Budget_Office%27s_Work_in_2018_-_A_Report_to_the_Congress.pdf
- **scanned**: Lyndon B. Johnson's desk diary page for Wednesday, 13 October 1954, a single scanned
  page with no text layer (it needs OCR; typed entries with handwritten notes). Held by the LBJ
  Library, via DPLA. Commons page:
  https://commons.wikimedia.org/wiki/File:Lyndon_Johnson%27s_1954_Desk_Diary_I,_10-13-1954_note_-_DPLA_-_00aeaf102c2df033a59aff9edc2dd2d3.pdf
- **table**: Congressional Budget Office, "Monthly Budget Review for December 2018" (8 January
  2019), 4 pages with a table on every page. Original: https://www.cbo.gov/publication/54905.
  Commons page: https://commons.wikimedia.org/wiki/File:Congressional_Budget_Office_report_2019-01-08_-_Monthly_Budget_Review_for_December_2018.pdf
