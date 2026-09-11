# Single-resume experiment

HH Agent historically keeps four logical resume keys in `data/resumes.yaml`:

- `project`
- `delivery`
- `technical_project`
- `product`

For an experiment with one physical HH.ru resume, do not delete those logical keys from the local config yet: the current matcher expects all four.

Use `configure_single_resume.py` to point all four logical keys to the same HH.ru resume ID and title while preserving matcher compatibility.

```powershell
.\.venv\Scripts\python.exe .\configure_single_resume.py `
  --resume-id "<HH_RESUME_ID>" `
  --title "<RESUME_TITLE>"
```

The script:

1. creates `data/resumes.yaml.bak-single-resume`;
2. copies the selected source resume metadata (default: `delivery`) to all four logical keys;
3. sets the same `hh_resume_id` and title for every logical key;
4. clears `generated_resumes`;
5. keeps `fallback_resume=delivery` by default.

For HH.ru this is enough: the browser worker uses the single resume available in the HH response form.

If Yandex/VK must use the same local PDF too, pass `--file-path`:

```powershell
.\.venv\Scripts\python.exe .\configure_single_resume.py `
  --resume-id "<HH_RESUME_ID>" `
  --title "<RESUME_TITLE>" `
  --file-path "data/resumes/<resume.pdf>"
```

`resume_raise_worker_v2.py` needs no special configuration: with one active resume on HH.ru it will simply see and raise the available resume.
