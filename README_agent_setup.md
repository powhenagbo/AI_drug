# Drug Discovery Agent Starter

This starter turns `drug_discovery_advanced_py3dmol.py` into a callable service.

## Files
- `drug_discovery_advanced_py3dmol.py` — your original scientific pipeline
- `agent_tools.py` — wrappers that expose the pipeline as tool-like functions
- `agent_server.py` — FastAPI API layer
- `requirements_agent.txt` — API dependencies
- `run_agent_api.sh` — local launch script

## 1) Install dependencies
```bash
pip install -r requirements_agent.txt
```

## 2) Start the API
```bash
bash run_agent_api.sh
```

## 3) Open the docs
```text
http://127.0.0.1:8000/docs
```

## 4) Example workflow request
```bash
curl -X POST "http://127.0.0.1:8000/workflow/run" \
  -H "Content-Type: application/json" \
  -d '{
    "disease_name": "diphtheria",
    "do_visuals": true,
    "do_ml": false,
    "do_archive": true
  }'
```

## 5) Example: one SMILES to an interactive 3D viewer
```bash
curl -X POST "http://127.0.0.1:8000/viewer/smiles" \
  -H "Content-Type: application/json" \
  -d '{
    "smiles": "CCO",
    "name": "ethanol"
  }'
```

## Notes
- Each disease gets its own output folder under `runs/`.
- The wrappers keep your original scientific logic intact.
- The API does not require an LLM. That means you can test the service first.
- After this works, you can add a chat or OpenAI layer on top of these endpoints.
