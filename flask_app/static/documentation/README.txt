Patient documentation (Hebrew PDFs) fallback location.

If DOCUMENTATION_DIR is not set and the repo "Documentation" folder is missing,
the app serves patient docs from here.

To fix broken /documentation/patient/oral_appliance_care links:

Option A - On server: set env var DOCUMENTATION_DIR to the absolute path of your
  Documentation folder (e.g. /var/app/Documentation).

Option B - Put the PDFs here with this structure:
  documentation/
    hebrew/
      הוראות למטופל/
        הוראות למטופל - טיפול ושימוש בהתקן האוראלי.pdf
        הנחיות לאחר קבלת התקן אורלי לטיפול בדום נשימה חסימתי בשינה.pdf
        הסכמה מדעת לטיפול בהפרעות נשימה הקשורות לשינה.pdf

Option C - Or place those 3 PDF files directly in this folder (documentation/).
  The app will find them via the legacy path.
