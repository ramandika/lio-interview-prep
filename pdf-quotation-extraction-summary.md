# Ekstraksi PDF (Quotation) dengan LLM — Ringkasan Riset & Keputusan Arsitektur

> Hasil deep-research (5 search angles → 23 sumber → 115 klaim → 25 diverifikasi adversarial: 24 confirmed, 1 refuted) + diskusi arsitektur.
> Tanggal: 2026-10-20

---

## 1. Temuan riset utama (terverifikasi)

### Pola pipeline modern
Per-template parser adalah masa lalu. Pola dominan:

```
Dokumen masuk
  → layout-aware transcription (OCR/Docling) + gambar halaman
  → schema-constrained LLM (Pydantic/JSON schema)
  → validasi + human review untuk tail
```

**Angka-angka kunci:**
- Fusing email body + OCR attachment: field terlewat **18.4% → 1.8%** (F1 0.745 → 0.951) — preprints.org/202607.0456
- Image-only extraction **hallucinate** — config terbaik = OCR Markdown + gambar bersamaan (Alan / medium.com/alan)
- Schema-constrained prompting sendiri menaikkan F1 **0.82 → 0.92**
- MINEA benchmark: akurasi LLM utuh hanya **0.780** → 100% tidak datang dari model
- Alan: **70% automation** end-to-end; Microsoft: review burden **−50%**, confidence triage memotong review **41%** sambil menangkap >80% error
- LoRA LLaMA 3-8B + **308 contoh teranotasi**: F1 0.812 → 0.947 (on-premise, murah)
- 7B open-weight VLM: 94.8% vs GPT-4V 96.4% (DataUnchain)

### Resep "almost 100% accuracy"
Tidak dari model, tapi dari engineering di sekelilingnya:
1. Schema/Pydantic validation → gagal = re-prompt (maks ~2×) / review
2. Confidence threshold per-field, dikalibrasi terhadap labeled data (+ *policy terpisah untuk field kosong* — "missing" ≠ "missed")
3. Cross-check bisnis: aritmetika (qty×price), SKU↔katalog, ERP/CRM
4. Backtest harness: re-run pipeline penuh pada ground truth, diff field-by-field dengan criticality weight
5. Ramp-up: 100% review manusia untuk hari-hari pertama dokumen kategori baru
6. Koreksi manusia → few-shot library / model memory (RAG, tanpa retraining — pola Affinda)

### Template variation — 3 strategi (jangan bikin parser per-template)
1. Reframe sebagai **matching**, bukan parsing (Davinci: "variation is not in the layout but in the language")
2. **RAG model memory**: dokumen baru di-match ke contoh tervalidasi, koreksi langsung propagate (Affinda)
3. **VLM + schema + beberapa exem par** (Alan — mengalahkan one-prompt-per-template)

### Multibahasa
Bahasa = input property, bukan masalah template terpisah. Affinda klaim 50+ bahasa. ⚠️ Klaim multibahasa spesifik (Arab/Mandarin/Rusia/Hindi di pipeline AWS) **gagal verifikasi** — bagian paling lemah buktinya; jika dokumen non-Latin, siapkan eval set sendiri.

---

## 2. Kapan LLM dipakai (dan kapan tidak)

```
1. Pre-LLM  (murah, tanpa LLM): deteksi text layer, OCR, fusion teks+gambar
2. LLM      (inti):             ekstraksi schema-constrained ← variasi template & bahasa
3. Post-LLM (murah):            Pydantic validation, aritmetika, ERP check;
                                JSON invalid → re-prompt LLM
4. Escalation (LLM mahal):      tail yang gagal validasi murah → frontier model → manusia
```

BuKAN tugas LLM: OCR, aritmetika, validasi schema — murahnya, deterministik, auditable.

**Mode input PDF ke LLM:**
- PDF langsung (Claude/Gemini API) = otomatis teks+gambar → paling praktis
- Image-only saja = ❌ paling hallucinate
- **OCR teks + gambar bersamaan = ✅ terbaik (terverifikasi 2 sumber independen)**
- Untuk quotation via email: fuse email body + teks attachment

---

## 3. LangChain: perannya tipis tapi nyata

- **Nilai satu-satunya yang wajib:** `with_structured_output` — menyeragamkan mekanisme schema-binding per-provider (tool calling / JSON mode / native structured output).
- **Nilai strategis:** swap model tanpa ubah kode — frontier → LoRA'd LLaMA via vLLM cukup ganti satu baris.
- Tidak perlu: document loaders, LCEL chains, retrievers.
- Alternatif setara: **PydanticAI** / **instructor** (lebih ramping).
- Pola penting dari tutorial resmi: semua field `Optional` + deskripsi kaya (jangan paksa LLM mengarang), few-shot **termasuk negative example** (mengajari return kosong saat entitas absen).

## 4. LangGraph: keputusan = TIDAK untuk v1

- Shared state antar node itu nyata (mekanismenya: satu `State`, node mengembalikan partial update yang di-merge).
- Tapi shared state tidak butuh LangGraph — dict Python cukup.
- Yang benar-benar dibeli LangGraph: **checkpointer + interrupt/resume** (eksekusi hidup di tengah) — hanya terbayar kalau review manusia diimplement sebagai "pause graph".
- **Keputusan desain:** review sebagai **kolom status di DB** (`auto_ok | needs_review | done`), bukan eksekusi yang parkir → LangGraph drop.
- Riset sendiri tidak pernah membutuhkannya: AWS Rhubarb = jsonschema + loop; pipeline procurement = tanpa graph.

**Trigger evaluasi ulang (nyata, bukan spekulasi):**
- alur multi-branch per dokumen yang kompleks, atau
- kebutuhan replai/time-travel debug ekstraksi massal, atau
- review yang benar-benar resume eksekusi (escalate frontier setelah koreksi manusia)

**Asuransi migrasi:** tulis `triage`/`extract`/`validate` sebagai fungsi murni (dict-in, dict-out) sejak awal → menambah StateGraph belakangan hanya mengubah pemanggilan.

---

## 5. Arsitektur final (v1, tanpa LangGraph)

```
                      ┌────────────┐
   PDF upload ───────►│   triage   │  text layer? scan? (murah)
                      └──────┬─────┘
                     ┌───────┴────────┐
               ┌─────▼─────┐    ┌─────▼────┐
               │parse_text │    │ocr_fuse  │     PyPDF2/Docling | OCR+gambar
               └─────┬─────┘    └─────┬────┘      (jalan di background worker)
                     └───────┬────────┘
                       ┌─────▼──────┐
                       │  extract   │  LLM + structured_output(Quotation)
                       └─────┬──────┘    field Optional + few-shot ± negative
                             │           (loop re-prompt maks 2× saat gagal)
                       ┌─────▼──────┐
                       │  validate  │  aritmetika, SKU↔katalog, ERP
                       └─────┬──────┘
                       ┌─────▼──────┐
                       │triage_conf │  threshold per-field
                       └─────┬──────┘
                 ┌───────────┴──────────┐
           ┌─────▼─────┐          ┌─────▼───────┐
           │  persist  │          │ human_review│   status kolom DB,
           └─────┬─────┘          └─────┬───────┘   bukan pause program
                 └──────────┬───────────┘
                        (koreksi → tabel corrections
                         → few-shot library → nanti: dataset LoRA ~300 dok.)
```

**Komponen:**

| Bagian | Pilihan |
|---|---|
| Ekstraksi | LangChain `with_structured_output` ATAU PydanticAI/instructor |
| Model | Frontier (Claude) untuk v1; LoRA 8B via vLLM setelah ~300 koreksi |
| OCR | Docling / Mistral OCR (~$2/1k halaman) untuk dokumen scan |
| API | FastAPI: `POST /quotations` (202 + doc_id, background), `GET /quotations/{id}`, `POST /quotations/{id}/review` |
| DB | dokumen (file asli immutable) + hasil + kolom status + tabel `corrections` |
| Persist | upsert keyed `(supplier, quote_number)` — handle revisi PDF sebagai delta "changed_fields" |
| Eval | backtest harness: re-run pipeline pada ground truth, diff field-by-field |

---

## 6. Open questions dari riset (belum terjawab oleh sumber)

- Kalibrasi threshold saat generic LLM API tanpa confidence score → self-consistency voting?
- Head-to-head biaya: frontier-only vs routing-tiered vs fine-tuned-small pada workload sama
- Self-hosted VLM (Qwen-VL/MinerU/PaddleOCR-VL) vs cloud document-AI untuk quotation non-Latin

## 7. Sumber utama

- LangChain extraction tutorial (python.langchain.com/docs/tutorials/extraction/)
- Alan — engineering blog (medium.com/alan) — pipeline asuransi 70% automation
- Microsoft Content Understanding: confidence/grounding/HITL (devblogs.microsoft.com/foundry)
- MINEA — arxiv.org/html/2404.04068v1 (eval tanpa ground truth manual)
- AWS Rhubarb — github.com/awslabs/rhubarb (re-prompt on schema validation failure)
- Procurement quotation preprint — preprints.org/manuscript/202607.0456 (F1 0.951, LoRA 8B)
- Affinda quotation parser + docs (model memory, 50+ bahasa)
- Davinci Solutions — semantic matching untuk quote requests
- DataUnchain VLM benchmark 2026; PaddleOCR-VL-1.6 (OmniDocBench 96.33%, 100+ bahasa)

⚠️ Semua angka vendor = self-report; angka preprint = peer-review pending, single-domain. Perlakukan angka sebagai directional, pola sebagai solid.