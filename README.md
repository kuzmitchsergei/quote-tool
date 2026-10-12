# Quote Check

Supplier quotation analysis for industrial spare parts, running on a local AI.

> **Prototype for demonstration.** All suppliers, part numbers and prices in this repository are fictional.

## The problem

Buyers of industrial equipment regularly receive spare-parts quotations with price increases, often applied across the board and justified with "raw materials" or "market conditions". Checking every line against past prices, and every argument against real market data, takes hours. So it often doesn't happen, and the increase goes through.

## What it does

1. **Reads the quotation.** Python reads the price table from the PDF and checks that the lines add up to the quoted total. The local AI reads the cover letter and extracts the supplier, the quote details and every reason given for price changes.
2. **Compares.** Every line is checked against the price history and benchmark indices. Anomalies are flagged, such as uniform increases across unrelated parts or increases larger than the stated reason can explain.
3. **Drafts the question.** It writes a reply to the supplier asking for justification, in the chosen tone and language.
4. **Checks the answer.** It reads the supplier's reply, sorts each claim into a category, and tests material and sub-supplier arguments against market data (World Bank and Eurostat series).
5. **Keeps the case.** Every negotiation is stored as a case, across several rounds.

## Design principles

- **Numbers come from code, never from the AI.** All comparisons and calculations are plain Python.
- **Guards on AI text.** If a drafted email contains a figure that isn't in the data, it's replaced before anyone sees it.
- **Rule-based cross-checks.** Claims the AI classifies are checked again by fixed rules, which catches misreadings by the model.
- **Local only.** The AI runs on the user's own computer via Ollama. No quotation data goes to a cloud service.

## Results

On three fictional demo suppliers (OEM Alpha, OEM Beta, OEM Gamma) with 16 planted price anomalies, Quote Check detected all 16 with no false alarms. This is a small, self-built test set, not a production benchmark.

## Project structure

| File | Purpose |
| --- | --- |
| `app.py` | Web interface (Streamlit) |
| `extract.py` | Reads the quotation PDF and cover letter |
| `compare.py` | Compares lines with price history and benchmarks |
| `draft_email.py` | Drafts the justification request |
| `analyze_reply.py` | Analyses the supplier's reply and drafts the follow-up |
| `market.py` | Checks material and sub-supplier claims |
| `market_data.py` | Imports and validates market price series |
| `cases.py` | Stores multi-round negotiations |
| `mailfile.py` | Reads `.eml` and `.msg` email files |
| `score.py` | Scores supplier claims against the actual price changes |
| `generate_demo_data.py` | Creates the fictional demo suppliers and quotes |

## Running it

Requirements: macOS or Linux, Python 3, and [Ollama](https://ollama.com) with the `gemma4:e2b` model.

```
ollama pull gemma4:e2b
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Limits

This is a prototype. A small local model can misread documents, so every output is meant for human review before anything is sent. It has not been tested on real customer data.

## Author

[Sergei Kuzmitch](https://www.linkedin.com/in/sergeikuzmitch): 12 years in key account management for capital equipment in the security printing industry. Built with Claude as a pair programmer.
