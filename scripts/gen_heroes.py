#!/usr/bin/env python3
import os
"""Generate missing hero images for all ArchonHQ series using gpt-image-2."""
import requests, os, base64, time, subprocess
from pathlib import Path

env = {}
for line in Path(os.path.expanduser("~/.hermes/.env")).read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

openai_key = env.get('OPENAI_API_KEY', '')
hero_dir = Path(os.environ.get("CONTENT_ENGINE_HERO_IMAGES_DIR", "hero-images"))

# Collect all missing heroes
missing = []
for series in ["caliber-series", "shipyard-series", "signal-series", "forge-series", "crucible-series", "bastion-series"]:
    d = Path(os.environ.get("CONTENT_ENGINE_ARTICLES_DIR", "articles")) / series
    if d.exists():
        for f in sorted(d.glob("*.md")):
            slug = f.stem
            hero = hero_dir / f"{slug}_hero.png"
            if not hero.exists():
                missing.append(slug)

print(f"Missing heroes: {len(missing)}")

# Visual prompts per article
VISUALS = {
    # Signal series
    "G01": "A terminal window with colorful skill modules being installed and connected by glowing neural pathways, each module a different color representing different AI capabilities",
    "G02": "Professional engineering team structure mapped as a neural network, roles connected by data flow arrows, sprint boards and architecture diagrams floating in dark space",
    "G03": "A minimalist code editor with an AI agent making autonomous edits, diff highlights in green and red, cursor moving by itself through clean code",
    "G04": "VS Code interface with a custom AI agent panel, no GitHub Copilot branding, agent making intelligent suggestions with tool call visualizations",
    "G05": "Hermes-like agent architecture blueprint with multiple agent nodes, skill modules, memory banks, and communication channels in a hub-spoke layout",
    "G06": "A tiny 26-million parameter model represented as a compact glowing core, surrounded by tool call interfaces radiating outward like spokes",
    "G07": "A deterministic pipeline with edge cases highlighted as warning signs, guardrails and validation checkpoints along a flowing data stream",
    "G08": "A CPU chip schematic rendered as art, logic gates and arithmetic units glowing with data flowing through Verilog-like pathways",
    "G09": "Claude AI head with perfect memory banks glowing behind it, conversation threads weaving into structured memory blocks, infinite recall visualization",
    "G10": "A laptop with GPU meters showing 6GB VRAM usage, cooling fans visualized, local LLM inference running with token counters",
    "G11": "A software 3.0 label crossed out with engineering expertise symbol glowing brighter, LLM outputs being refined by human expertise filter",
    "G12": "A content production pipeline like a factory conveyor belt, raw ideas entering, processed articles exiting, automated stages in between",
    # Forge series
    "F01": "An MCP server architecture blueprint, JSON-RPC messages flowing through stdio transport, tool discovery handshake visualized as glowing protocol exchange",
    "F02": "Three lock symbols representing API key, OAuth2, and zero-trust auth patterns, each guarding a different MCP server gateway, shield symbols",
    "F03": "JSON Schema documents being designed and validated, tool discovery interface showing parameter types and examples, version tags v1 v2",
    "F04": "Multiple MCP servers connected through a central gateway router, mesh topology with peer connections, chained pipeline with data flowing between servers",
    "F05": "Streaming data packets flowing through MCP server channels, timeout gauges showing per-tool limits, cancellation signals interrupting long operations",
    "F06": "Smart home devices connected to an MCP brain, lights switches sensors climate controls linked by protocol lines, Home Assistant dashboard",
    # Crucible series
    "C01": "Split screen: clean orderly dev environment on left, chaotic production with red error cascades on right, diagnostic probes investigating",
    "C02": "Testing harness framework surrounding an AI agent, green checkmarks and red X marks, test pyramid with unit integration regression layers",
    "C03": "Observability dashboard with distributed tracing timelines, structured log streams, metric gauges, and alert thresholds for AI agents",
    "C04": "Checkpoint markers along an agent execution path, state machine with recovery arrows, deduplication hash symbols preventing double execution",
    "C05": "CI CD pipeline conveyor belt moving AI agent changes through test stages into deployment gate, GitHub Actions workflow steps",
    "C06": "Checklist with 12 determinism dimensions each with risk level indicator, audit shield, consistency verification hash comparisons",
    # Bastion series
    "B01": "A laptop running local LLM inference, GPU meters and token counters visible, privacy shield symbol, model weights loading into memory",
    "B02": "Private knowledge base vault with document chunks being embedded into a local vector store, RAG pipeline with lock symbols on data flows",
    "B03": "Air-gapped server room with isolated AI agent running in a sealed environment, no network cables, firewall wall visualization",
    "B04": "Hybrid architecture split between cloud and local, data classification labels routing requests, fallback arrows and cost comparison meters",
    "B05": "Jetson board and Raspberry Pi running AI models, thermal gauges, power meters, quantized model weights, edge device landscape",
    "B06": "Compliance matrix with GDPR and HIPAA checkmarks, data flow map with residency markers, shield and lock symbols, audit report",
}

SERIES_PALETTES = {
    "G": "Plasma violet and arctic blue accents",
    "F": "Molten amber and electric cyan accents",
    "C": "Molten amber and steel grey accents",
    "B": "Verdant emerald and arctic blue accents",
}

for slug in missing:
    prefix = slug.split("-")[0]
    visual = VISUALS.get(prefix, "Abstract AI technology concept")
    palette = SERIES_PALETTES.get(prefix[0], "subtle gradient accents")
    
    prompt = f"Minimalist digital illustration: {visual}. {palette}. Dark theme, clean lines, professional tech blog hero image style. No text."
    
    try:
        r = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
            json={"model": "gpt-image-2", "prompt": prompt, "n": 1, "size": "1536x1024", "quality": "high"},
            timeout=180
        )
        if r.status_code == 200:
            data = r.json()["data"][0]
            img_data = base64.b64decode(data["b64_json"])
            raw_path = hero_dir / f"{slug}_hero_raw.png"
            raw_path.write_bytes(img_data)
            
            final_path = hero_dir / f"{slug}_hero.png"
            crop_cmd = (
                f"from PIL import Image; "
                f"img=Image.open('{raw_path}'); w,h=img.size; tr=16/9; cr=w/h; "
                f"nw=int(h*tr) if cr>tr else w; nh=int(w/tr) if cr<=tr else h; "
                f"l=(w-nw)//2 if cr>tr else 0; t=(h-nh)//2 if cr<=tr else 0; "
                f"img.crop((l,t,l+nw,t+nh)).resize((1920,1080),Image.LANCZOS).save('{final_path}','PNG')"
            )
            subprocess.run(["python3.12", "-c", crop_cmd], check=True, timeout=15)
            raw_path.unlink()
            size_mb = final_path.stat().st_size / 1024 / 1024
            print(f"✅ {prefix} ({size_mb:.1f}MB)")
        else:
            print(f"❌ {prefix}: HTTP {r.status_code} — {r.text[:150]}")
            if r.status_code == 429:
                print("   Rate limited, waiting 30s...")
                time.sleep(30)
    except Exception as e:
        print(f"❌ {prefix}: {str(e)[:80]}")
    
    time.sleep(5)

print("\n🎉 All hero images complete")
