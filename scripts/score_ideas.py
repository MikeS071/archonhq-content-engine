#!/usr/bin/env python3
"""Score ideas from the daily scan against active series themes.

Usage:
  python3 score_ideas.py                          # re-score all ideas
  python3 score_ideas.py --add "Title" --tier T1  # manually add an idea
  python3 score_ideas.py --list                   # show scored ideas
  python3 score_ideas.py --best                   # show top 3 by best-fit score
  python3 score_ideas.py --new-series             # show ideas that don't fit any series

Scoring has two layers:
  1. Criteria layer: 0-3 per fit_criteria criterion (must_have * 3, nice_to_have * 1, disqualify * -5)
  2. Identity layer: idea text scored against series description, dna, tone, and core_themes
     (0-3 per identity dimension, weighted by 2)

Final fit = criteria_score + identity_score.
Max possible = criteria_max + identity_max.
"""
import argparse, json, os, sys
from pathlib import Path

THEMES_DIR = Path("/home/hermes/archonhq-content/series_themes")
CATALOGUE = Path("/home/hermes/archonhq-content/idea_catalogue.json")

# Semantic concept groups — ideas matching these keywords score higher
CONCEPT_MAP = {
    # Caliber business concepts
    "business": ["icp", "ideal customer", "market", "positioning", "niche", "audience",
                 "founder", "solopreneur", "freelancer", "consult", "agency", "revenue",
                 "income", "scale", "growth", "6 figures", "six figures", "income"],
    "offer_pricing": ["offer", "price", "pricing", "value equation", "package",
                      "consulting package", "rate", "charge", "invoice", "tier",
                      "premium", "freemium", "monetiz"],
    "content_hooks": ["hook", "scroll", "attention", "headline", "open rate",
                      "click", "ctr", "engagement", "viral", "share", "anti-pattern"],
    "prompt_engineering": ["prompt", "context engineering", "meta-prompt", "system prompt",
                           "few-shot", "chain-of-thought", "token", "llm", "gpt",
                           "claude", "gemini", "openrouter"],
    "learning_science": ["learn", "skill", "decompos", "deliberate practice", "acquisition",
                         "retention", "spacing", "three month wall", "plateau"],
    "evidence": ["evidence", "data", "study", "research", "equation",
                 "metric", "backed", "science"],

    # Shipyard tool-building concepts
    "cli_tool": ["cli", "command line", "terminal", "argparse", "script", "build your own",
                 "package manager", "install", "generator", "scaffold"],
    "api_integration": ["api", "openrouter", "gemini api", "github api", "webhook",
                        "endpoint", "rest", "sdk", "integration"],
    "automation": ["automat", "pipeline", "agent", "orchestrat", "workflow", "cron",
                   "ci/cd", "github actions", "deploy", "batch"],
    "downloadable": ["download", "module", "package", "artifact", "ship", "installer"],

    # Signal engineering opinion concepts
    "opinionated_take": ["opinion", "thesis", "conventional wisdom", "challenge", "debunk",
                         "slop", "silver bullet", "craftsmanship", "wrong"],
    "technical_depth": ["design pattern", "deconstruct", "clone", "internals",
                        "runtime", "protocol", "abstraction", "determinism"],
    "local_inference": ["local", "laptop", "6gb", "vram", "edge", "small model", "gguf",
                        "cpu", "gpu", "low resource", "constrained"],
    "build_walkthrough": ["build your own", "clone", "from scratch", "lines of code",
                          "how i built", "walkthrough", "under the hood"],

    # Forge MCP architecture concepts
    "mcp_protocol": ["mcp", "model context protocol", "mcp server", "tool discovery", "tool calling",
                     "mcp client", "context protocol", "server protocol"],
    "mcp_auth": ["oauth", "zero-trust", "api key", "auth pattern", "bearer token", "jwt",
                 "permission", "access control"],
    "mcp_transport": ["stdio", "websocket", "http transport", "streaming", "long-running",
                      "sse", "json-rpc"],
    "mcp_composition": ["composition", "multi-server", "orchestration", "server registry",
                        "tool routing", "fallback", "server chain"],

    # Crucible agent reliability concepts
    "agent_testing": ["test", "harness", "regression", "ci/cd", "assertion", "evaluation",
                      "benchmark", "quality gate", "test suite"],
    "agent_observability": ["observability", "logging", "tracing", "monitoring", "alerting",
                            "health check", "heartbeat", "metrics", "dashboard"],
    "agent_reliability": ["reliability", "determinism", "idempotent", "retry", "failure recovery",
                          "graceful degradation", "flaky", "silent failure", "production"],
    "agent_safety": ["safety", "guardrail", "validation", "output check", "hallucination detect",
                     "content filter", "constraint"],

    # Bastion privacy-first concepts
    "privacy_local": ["self-hosted", "local-first", "offline", "air-gapped", "on-premise",
                      "privacy", "data sovereignty", "no cloud"],
    "privacy_regulation": ["gdpr", "hipaa", "compliance", "regulation", "data protection",
                          "pii", "sensitive data", "consent"],
    "privacy_hardware": ["jetson", "raspberry pi", "edge device", "arm", "low vram",
                         "quantized", "gguf", "local inference"],
    "privacy_encrypted": ["encrypted", "confidential", "zero-knowledge", "homomorphic",
                          "secure enclave", "tee"],

    # Keystone enterprise architecture concepts
    "ea_contradictions": ["contradiction", "tension", "trade-off", "paradox", "unsaid",
                          "unspeakable", "conflict", "dilemma", "double bind"],
    "ea_vendor_lockin": ["vendor", "entanglement", "lock-in", "portability", "abstraction layer",
                         "anti-corruption", "dependency inversion", "value-add", "flexibility"],
    "ea_coupling": ["loose coupling", "api vs event", "event-driven", "bounded context",
                    "coupling direction", "postel", "schema", "contract-first", "eda"],
    "ea_resilience": ["circuit breaker", "timeout", "cascading failure", "load shedding",
                      "fail fast", "steady state", "resilience pattern", "error budget",
                      "sre", "slo", "chaos engineering", "slow response"],
    "ea_data_model": ["canonical data model", "data envy", "domain model", "data ownership",
                      "data mesh", "bounded context", "data as asset", "eventual consistency"],
    "ea_tech_debt": ["technical debt", "run debt", "change debt", "lifecycle", "remediation",
                     "sustain", "contain", "exit", "decommission", "alignment score"],
    "ea_security": ["zero trust", "defense in depth", "least privilege", "secure by design",
                    "built-in security", "iam", "segregation", "compartmentalise"],
    "ea_governance": ["architecture principle", "governance", "alignment worksheet",
                      "capability", "operating model", "conway", "platform mindset"],
    "ea_ai_slop": ["software slop", "ai slop", "ai code quality", "specialist agent",
                   "harness", "dev harness", "code guard", "verification loop"],
    "ai_governance": ["ai governance", "ai policy", "responsible ai", "ai ethics", "model governance",
                      "ai risk", "ai compliance", "model risk", "ai audit", "ai oversight",
                      "algorithmic accountability", "ai regulation", "eu ai act",
                      "ai safety board", "model validation", "ai transparency",
                      "explainability", "fairness", "bias detect", "ai impact assessment"],
    "data_governance": ["data governance", "data management", "data quality", "data lineage",
                        "data stewardship", "data lifecycle", "data catalog", "data ownership",
                        "data classification", "data retention", "master data", "mdm",
                        "data mesh governance", "data product", "data democratization",
                        "data platform", "data strategy", "data office", "cdo"],
    "knowledge_architecture": ["knowledge base", "knowledge graph", "knowledge management",
                               "compounding knowledge", "schema-first", "structured wiki",
                               "ontology", "taxonomy", "thesaurus", "semantic",
                               "agents.md", "ingestion pipeline", "knowledge curation",
                               "cross-reference", "contradiction detection", "gap analysis"],
    "graph_databases": ["graph database", "neo4j", "sparql", "rdf", "cypher", "property graph",
                        "triple store", "node", "edge", "relationship", "traversal",
                        "vertex", "gremlin", "ontotext", "stardog", "neptune"],
    "rag_patterns": ["rag", "retrieval augmented", "vector store", "vector database",
                     "embedding", "similarity search", "chunking", "retrieval",
                     "hybrid search", "reranking", "query decomposition"],
    "ai_memory": ["memory system", "episodic memory", "long-term memory", "short-term memory",
                  "working memory", "context window", "knowledge compounding",
                  "hallucination", "error compounding", "context limit"],
    "kb_health": ["linting", "gap analysis", "decay detection", "knowledge decay",
                  "stale content", "orphan page", "broken link", "coverage gap",
                  "freshness", "validation", "health check"],
}

# Series identity mapping: which concept groups define each series
# This links series DNA/description/core_themes to concept groups for identity scoring
SERIES_IDENTITY = {
    "Caliber": {
        "description_keywords": ["business", "growth", "revenue", "framework", "equation"],
        "concept_groups": ["business", "offer_pricing", "content_hooks", "evidence", "learning_science"],
        "style": "strategic frameworks with evidence, not tutorials",
    },
    "Shipyard": {
        "description_keywords": ["cli", "tool", "build", "download", "package", "script"],
        "concept_groups": ["cli_tool", "api_integration", "automation", "downloadable"],
        "style": "ships a working tool you can download and run",
    },
    "Signal": {
        "description_keywords": ["opinion", "thesis", "deep-dive", "debunk", "architecture"],
        "concept_groups": ["opinionated_take", "technical_depth", "build_walkthrough", "local_inference"],
        "style": "opinionated engineering positions with code proof",
    },
    "Forge": {
        "description_keywords": ["mcp", "protocol", "server", "tool calling", "context protocol"],
        "concept_groups": ["mcp_protocol", "mcp_auth", "mcp_transport", "mcp_composition"],
        "style": "MCP protocol and server architecture with working implementations",
    },
    "Crucible": {
        "description_keywords": ["reliability", "testing", "agent", "production", "harness"],
        "concept_groups": ["agent_testing", "agent_observability", "agent_reliability", "agent_safety"],
        "style": "agent reliability patterns with testing/monitoring tools",
    },
    "Bastion": {
        "description_keywords": ["privacy", "offline", "local", "self-hosted", "sovereignty"],
        "concept_groups": ["privacy_local", "privacy_regulation", "privacy_hardware", "privacy_encrypted"],
        "style": "privacy-first and offline/local AI with self-hosted systems",
    },
    "Keystone": {
        "description_keywords": ["enterprise", "architecture", "governance", "contradiction", "tension",
                                 "decision framework", "principle", "debt", "resilience", "vendor",
                                 "data management", "data governance", "ai governance"],
        "concept_groups": ["ea_contradictions", "ea_vendor_lockin", "ea_coupling", "ea_resilience",
                           "ea_data_model", "ea_tech_debt", "ea_security", "ea_governance",
                           "ea_ai_slop", "ai_governance", "data_governance"],
        "style": "enterprise architecture principles, contradictions, and governance — names the unsayable",
    },
    "Atlas": {
        "description_keywords": ["knowledge base", "knowledge graph", "compounding", "schema",
                                 "rag", "vector store", "ontology", "semantic", "memory",
                                 "ingestion", "curation", "graph database"],
        "concept_groups": ["knowledge_architecture", "graph_databases", "rag_patterns",
                           "ai_memory", "kb_health", "data_governance"],
        "style": "pragmatic knowledge architecture — schema, query, compounding loop, honest breakpoints",
    },
}


def load_themes():
    themes = {}
    for f in sorted(THEMES_DIR.glob("*.json")):
        data = json.loads(f.read_text())
        themes[data["series"]] = data
    return themes


def load_catalogue():
    return json.loads(CATALOGUE.read_text())


def save_catalogue(cat):
    CATALOGUE.write_text(json.dumps(cat, indent=2))


def semantic_score(text, criterion):
    """Score a text against a criterion using concept map + direct keyword overlap.
    Returns 0-3: 0=miss, 1=weak, 2=partial, 3=strong.
    Supports both plain string and dict {"criterion": ..., "weight": ...} format."""
    text_lower = text.lower()
    # Handle both plain string and dict format
    if isinstance(criterion, dict):
        crit_lower = criterion.get("criterion", "").lower()
    else:
        crit_lower = criterion.lower()

    # Direct keyword match on criterion words
    crit_words = [w for w in crit_lower.split() if len(w) > 3]
    direct_matches = sum(1 for w in crit_words if w in text_lower)
    direct_ratio = direct_matches / len(crit_words) if crit_words else 0

    # Concept map match — find concepts related to the criterion
    concept_hits = 0
    concept_total = 0
    for concept_name, keywords in CONCEPT_MAP.items():
        # Does this concept relate to the criterion?
        concept_words = concept_name.replace("_", " ").split()
        concept_match = any(cw in crit_lower for cw in concept_words)
        if concept_match:
            # Check if the idea text hits any keyword in this concept group
            concept_total += 1
            if any(kw in text_lower for kw in keywords):
                concept_hits += 1

    # Also check: does the text hit concepts that the criterion implies?
    for concept_name, keywords in CONCEPT_MAP.items():
        if any(kw in text_lower for kw in keywords):
            # This concept is present in the text
            # Check if the criterion is about this concept
            concept_label = concept_name.replace("_", " ")
            if concept_label in crit_lower:
                concept_hits += 1
                concept_total += 1

    # Combined score
    if direct_ratio >= 0.7 or (concept_total > 0 and concept_hits / concept_total >= 0.7):
        return 3
    elif direct_ratio >= 0.4 or (concept_total > 0 and concept_hits / concept_total >= 0.4):
        return 2
    elif direct_ratio > 0 or concept_hits > 0:
        return 1
    return 0


def score_identity(text, series_name):
    """Score an idea's text against a series' identity: description, DNA, concept groups, and style.
    Returns 0-3 per dimension, total 0-12."""
    identity = SERIES_IDENTITY.get(series_name)
    if not identity:
        return {"total": 0, "max": 12, "details": {}}

    text_lower = text.lower()
    details = {}

    # 1. Description keyword match (0-3)
    desc_kw = identity["description_keywords"]
    desc_hits = sum(1 for kw in desc_kw if kw in text_lower)
    desc_ratio = desc_hits / len(desc_kw) if desc_kw else 0
    # Boost: even 1 hit out of many is meaningful if the keyword is specific
    if desc_ratio >= 0.5:
        desc_score = 3
    elif desc_ratio >= 0.25:
        desc_score = 2
    elif desc_hits >= 2:
        desc_score = 2
    elif desc_hits > 0:
        desc_score = 1
    else:
        desc_score = 0
    details["description"] = {"score": desc_score, "hits": desc_hits, "total": len(desc_kw)}

    # 2. Concept group coverage (0-3) — how many of this series' concept groups does the text hit?
    groups = identity["concept_groups"]
    group_hits = 0
    for group_name in groups:
        keywords = CONCEPT_MAP.get(group_name, [])
        if any(kw in text_lower for kw in keywords):
            group_hits += 1
    group_ratio = group_hits / len(groups) if groups else 0
    # Boost: even hitting 1 concept group is meaningful for series with many groups
    if group_ratio >= 0.4:
        group_score = 3
    elif group_ratio >= 0.2:
        group_score = 2
    elif group_hits >= 1:
        group_score = 1
    else:
        group_score = 0
    details["concept_groups"] = {"score": group_score, "hits": group_hits, "total": len(groups)}

    # 3. Style alignment (0-3) — check if the text matches the series' editorial style
    style = identity["style"].lower()
    style_words = [w for w in style.split() if len(w) > 3]
    style_hits = sum(1 for w in style_words if w in text_lower)
    style_ratio = style_hits / len(style_words) if style_words else 0
    style_score = 3 if style_ratio >= 0.4 else (2 if style_ratio >= 0.2 else (1 if style_ratio > 0 else 0))
    details["style"] = {"score": style_score, "hits": style_hits, "total": len(style_words)}

    # 4. Core themes overlap (0-3) — check against theme JSON's included themes
    # This is populated dynamically from the theme file
    details["core_themes"] = {"score": 0, "hits": 0, "total": 0}

    total = desc_score + group_score + style_score
    return {"total": total, "max": 9, "details": details}  # max 9 (3 dimensions, core_themes scored separately)


def score_core_themes(text, theme):
    """Score idea text against a series' core_themes.included list from the theme JSON.
    Returns 0-3."""
    core_themes_raw = theme.get("core_themes", {})
    if isinstance(core_themes_raw, list):
        included = core_themes_raw
    else:
        included = core_themes_raw.get("included", [])

    if not included:
        return 0, []

    text_lower = text.lower()
    hits = []
    for theme_str in included:
        theme_words = [w for w in theme_str.lower().split() if len(w) > 3]
        if any(w in text_lower for w in theme_words):
            hits.append(theme_str)

    ratio = len(hits) / len(included)
    score = 3 if ratio >= 0.3 else (2 if ratio >= 0.15 else (1 if ratio > 0 else 0))
    return score, hits


def score_idea(idea_title, idea_tier, themes, idea_synopsis=""):
    """Score an idea against all active series using criteria + identity + core_themes.
    Returns {series: {fit, max, pct, details}}."""
    results = {}

    # Combine title + synopsis for richer semantic matching
    scoring_text = idea_title
    if idea_synopsis:
        scoring_text = f"{idea_title}. {idea_synopsis}"

    for name, theme in themes.items():
        if theme["status"] != "active":
            continue

        criteria = theme["fit_criteria"]
        details = {"must_have": [], "nice_to_have": [], "disqualify": [], "identity": {}, "core_themes": {}}
        score = 0
        max_score = 0

        # === Layer 1: Fit criteria (existing behavior) ===
        # Must-have: each worth 0-3, multiplied by 3
        for crit in criteria["must_have"]:
            max_score += 9
            s = semantic_score(scoring_text, crit)
            score += s * 3
            details["must_have"].append({"criterion": crit if isinstance(crit, str) else crit.get("criterion", ""), "score": s})

        # Nice-to-have: each worth 0-3, multiplied by 1
        for crit in criteria["nice_to_have"]:
            max_score += 3
            s = semantic_score(scoring_text, crit)
            score += s * 1
            details["nice_to_have"].append({"criterion": crit if isinstance(crit, str) else crit.get("criterion", ""), "score": s})

        # Disqualify: each worth 0-3, multiplied by -5
        for crit in criteria["disqualify"]:
            s = semantic_score(scoring_text, crit)
            if s > 0:
                score -= s * 5
            details["disqualify"].append({"criterion": crit if isinstance(crit, str) else crit.get("criterion", ""), "score": s})

        # === Layer 2: Series identity (description, concept groups, style) ===
        identity = score_identity(scoring_text, name)
        identity_weight = 2  # each identity point worth 2
        score += identity["total"] * identity_weight
        max_score += identity["max"] * identity_weight
        details["identity"] = identity

        # === Layer 3: Core themes overlap ===
        ct_score, ct_hits = score_core_themes(scoring_text, theme)
        ct_weight = 3  # core themes hit worth 3
        score += ct_score * ct_weight
        max_score += 3 * ct_weight  # max 3 * 3 = 9
        details["core_themes"] = {"score": ct_score, "hits": ct_hits}

        pct = round(score / max_score * 100, 1) if max_score > 0 else 0
        results[name] = {"fit": score, "max": max_score, "pct": pct, "details": details}

    return results


def cmd_score(args):
    themes = load_themes()
    cat = load_catalogue()
    scored = 0

    for idea in cat["ideas"]:
        # Force re-score all (remove old scores)
        scores = score_idea(idea["title"], idea.get("tier", "T2"), themes, idea.get("synopsis", ""))
        idea["fit_scores"] = scores
        idea["best_fit"] = max(scores, key=lambda k: scores[k]["pct"]) if scores else None
        idea["best_fit_pct"] = scores[idea["best_fit"]]["pct"] if idea["best_fit"] else 0
        scored += 1

    if scored:
        save_catalogue(cat)
        print(f"Scored {scored} ideas")
    else:
        print("No ideas to score")


def cmd_add(args):
    cat = load_catalogue()
    idea = {
        "id": f"IDEA-{len(cat['ideas'])+1:03d}",
        "title": args.add,
        "tier": args.tier or "T2",
        "date": __import__("datetime").date.today().isoformat(),
        "source": args.source or "manual",
        "synopsis": args.synopsis or "",
        "fit_scores": {},
        "best_fit": None,
        "best_fit_pct": 0,
        "status": "new"
    }

    themes = load_themes()
    scores = score_idea(idea["title"], idea["tier"], themes, idea.get("synopsis", ""))
    idea["fit_scores"] = scores
    idea["best_fit"] = max(scores, key=lambda k: scores[k]["pct"]) if scores else None
    idea["best_fit_pct"] = scores[idea["best_fit"]]["pct"] if idea["best_fit"] else 0

    cat["ideas"].append(idea)
    save_catalogue(cat)
    print(f"Added {idea['id']}: {idea['title']}")
    print(f"  Best fit: {idea['best_fit']} ({idea['best_fit_pct']}%)")
    for series, s in scores.items():
        details_str = ", ".join(
            f"{d['criterion']}={d['score']}" for d in s["details"]["must_have"]
        )
        identity_total = s["details"].get("identity", {}).get("total", 0)
        identity_max = s["details"].get("identity", {}).get("max", 0)
        ct_score = s["details"].get("core_themes", {}).get("score", 0)
        print(f"  {series}: {s['pct']}% [criteria | identity: {identity_total}/{identity_max} | themes: {ct_score}/3]")


def cmd_list(args):
    cat = load_catalogue()
    if not cat["ideas"]:
        print("No ideas in catalogue")
        return
    header = '{:<10} {:<5} {:<12} {:>6}  {}'.format('ID', 'TIER', 'BEST FIT', 'FIT%', 'TITLE')
    print(header)
    print("-" * 70)
    for idea in sorted(cat["ideas"], key=lambda x: x.get("best_fit_pct", 0), reverse=True):
        fit = idea.get("best_fit", "none")
        pct = idea.get("best_fit_pct", 0)
        print('{:<10} {:<5} {:<12} {:>5.0f}%  {}'.format(idea['id'], idea['tier'], fit, pct, idea['title']))
        synopsis = idea.get('synopsis', '')
        if synopsis:
            # Truncate synopsis for display
            display = synopsis[:100] + ('...' if len(synopsis) > 100 else '')
            print('{:>25} {}'.format('', display))


def cmd_best(args):
    cat = load_catalogue()
    ranked = sorted(cat["ideas"], key=lambda x: x.get("best_fit_pct", 0), reverse=True)
    for idea in ranked[:3]:
        print(f"\n{idea['title']}")
        synopsis = idea.get('synopsis', '')
        if synopsis:
            print(f"  {synopsis[:200]}")
        print(f"  Best fit: {idea['best_fit']} ({idea['best_fit_pct']}%)")
        if idea["best_fit"] and idea["best_fit"] in idea.get("fit_scores", {}):
            details = idea["fit_scores"][idea["best_fit"]]["details"]
            for category in ["must_have", "nice_to_have", "disqualify"]:
                for d in details[category]:
                    if d["score"] > 0:
                        label = "+" if category != "disqualify" else "-"
                        print(f"    {label} {d['criterion']}: {d['score']}/3")
            # Show identity and core_themes
            identity = details.get("identity", {})
            if identity:
                print(f"    identity: {identity.get('total', 0)}/{identity.get('max', 0)}")
            ct = details.get("core_themes", {})
            if ct and ct.get("hits"):
                print(f"    core themes hit: {', '.join(ct['hits'][:3])}")


def cmd_new_series(args):
    cat = load_catalogue()
    threshold = args.threshold or 30
    candidates = []

    for idea in cat["ideas"]:
        pct = idea.get("best_fit_pct", 0)
        if pct < threshold:
            candidates.append((idea, pct))

    if not candidates:
        print(f"All ideas fit existing series above {threshold}% threshold")
        return

    candidates.sort(key=lambda x: x[1])
    print(f"Ideas below {threshold}% fit (potential new series):\n")
    for idea, pct in candidates:
        print("  {:>5.0f}%  {}".format(pct, idea['title']))
        print("        {} | {} | {}".format(idea['id'], idea.get('source', 'unknown'), idea['tier']))


def main():
    parser = argparse.ArgumentParser(description="Score ArchonHQ ideas against series themes")
    parser.add_argument("--add", metavar="TITLE", help="Add a new idea")
    parser.add_argument("--tier", choices=["T1", "T2"], help="Tier for new idea")
    parser.add_argument("--source", help="Source for new idea")
    parser.add_argument("--synopsis", help="Short synopsis for new idea")
    parser.add_argument("--list", action="store_true", help="List scored ideas")
    parser.add_argument("--best", action="store_true", help="Show top 3 ideas")
    parser.add_argument("--new-series", action="store_true", help="Show ideas that don't fit existing series")
    parser.add_argument("--threshold", type=int, default=30, help="Fit %% threshold for new-series (default 30)")
    args = parser.parse_args()

    if args.add:
        cmd_add(args)
    elif args.list:
        cmd_list(args)
    elif args.best:
        cmd_best(args)
    elif args.new_series:
        cmd_new_series(args)
    else:
        cmd_score(args)


if __name__ == "__main__":
    main()
