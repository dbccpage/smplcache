"""Automated Test Suite for the SimpleCache Research Forum Dossier.

This module implements a rigorous 4-tier verification test harness:
- Tier 1: Feature Coverage (file existence, non-emptiness, heading hierarchy)
- Tier 2: Boundary & Technical Scenarios (competitive systems, 5 scenarios, trade-off matrix, 5 upgrades, formal links)
- Tier 3: Cross-Feature Consistency (inter-document cross-referencing and architectural alignment)
- Tier 4: Claim Hygiene Enforcement (AST/regex forbidden words scan and 4-point claim audit verification)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pytest

# Resolve base paths
REPO_ROOT = Path(__file__).resolve().parent.parent
DOSSIER_DIR = REPO_ROOT / "docs" / "research_forum"

EXPECTED_FILES = [
    "README.md",
    "01_executive_whitepaper.md",
    "02_competitive_analysis.md",
    "03_commercial_upgrades.md",
    "04_cross_program_foundations.md",
    "05_research_forum_proceedings.md",
]

# Tier 4: Hardened forbidden patterns catching singular, plural, and stem variations
FORBIDDEN_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    ("proves/prove/proved/proving", re.compile(r"\b(proves|proving|proved|prove)\b", re.IGNORECASE)),
    ("guarantees/guarantee/guaranteed/guaranteeing", re.compile(r"\b(guarantees|guarantee|guaranteeing|guaranteed)\b", re.IGNORECASE)),
    ("mathematically verifies", re.compile(r"\bmathematically\s+(verifies|verified|verifying|verify)\b", re.IGNORECASE)),
    ("fundamentally/fundamental", re.compile(r"\b(fundamentally|fundamental)\b", re.IGNORECASE)),
    ("true intelligence", re.compile(r"\btrue\s+intelligence\b", re.IGNORECASE)),
    ("impossible", re.compile(r"\b(impossible|impossibility)\b", re.IGNORECASE)),
    ("universal", re.compile(r"\b(universal|universally|universality)\b", re.IGNORECASE)),
    ("flawless", re.compile(r"\b(flawless|flawlessly)\b", re.IGNORECASE)),
    ("perfectly", re.compile(r"\bperfectly\b", re.IGNORECASE)),
    ("clearly", re.compile(r"\bclearly\b", re.IGNORECASE)),
    ("obviously", re.compile(r"\bobviously\b", re.IGNORECASE)),
    ("simply", re.compile(r"\bsimply\b", re.IGNORECASE)),
    ("merely", re.compile(r"\bmerely\b", re.IGNORECASE)),
    ("weaponized", re.compile(r"\b(weaponized|weaponizing|weaponize)\b", re.IGNORECASE)),
]

# Backwards-compatible alias for module-level inspectability
FORBIDDEN_WORDS: List[str] = [label for label, _ in FORBIDDEN_PATTERNS]

# Approved contextual phrases where words like 'guarantees' are allowed under strict declared bounds
APPROVED_PHRASES: List[re.Pattern[str]] = [
    # 1. Guarantees strictly conditioned on declared assumptions, conditions, or bounds
    re.compile(r"guarantees?\s+(?:[a-zA-Z0-9_\s]{1,40}\s+)?under\s+(the\s+)?declared\s+(assumptions|conditions|bounds)", re.IGNORECASE),
    re.compile(r"guaranteed\s+(?:[a-zA-Z0-9_\s]{1,40}\s+)?under\s+(the\s+)?declared\s+(assumptions|conditions|bounds)", re.IGNORECASE),
    re.compile(r"guaranteeing\s+(?:[a-zA-Z0-9_\s]{1,40}\s+)?under\s+(the\s+)?declared\s+(assumptions|conditions|bounds)", re.IGNORECASE),
    # 2. Explicit guarantee negations
    re.compile(r"(?:does\s+not|do\s+not|cannot|no|without)\s+(?:claim\s+that\s+[a-zA-Z0-9_\s]{1,40}\s+)?guarantee[s]?", re.IGNORECASE),
    # 3. Proves strictly conditioned on declared assumptions
    re.compile(r"proves\s+(?:[a-zA-Z0-9_\s]{1,40}\s+)?under\s+(the\s+)?declared\s+assumptions", re.IGNORECASE),
    re.compile(r"proved\s+(?:[a-zA-Z0-9_\s]{1,40}\s+)?under\s+(the\s+)?declared\s+assumptions", re.IGNORECASE),
    # 4. Explicit proof negations
    re.compile(r"(?:does\s+not|do\s+not|cannot|no)\s+(?:claim\s+to\s+)?prove", re.IGNORECASE),
    # 5. Formal mathematical theorems from cited theory document (excludes implementation tests like Kernel/CLI)
    re.compile(r"Theorem\s+[0-9]+\s+[—\-]\s+[^\n(]+\((?:(?:One-Way\s+|Corrected\s+One-Way\s+)?Proved(?:\s+on\s+[a-zA-Z0-9_\s]+)?)\)", re.IGNORECASE),
    re.compile(r"Proved\s+Foundation:\s+Theorems\s+1\s+through\s+12", re.IGNORECASE),
    # 6. Universal negations
    re.compile(r"(?:does\s+not|do\s+not|no)\s+(?:claim\s+)?universal", re.IGNORECASE),
    re.compile(r"non[- ]universal", re.IGNORECASE),
    # 7. Domain-specific algebraic topology term (fundamental cycle basis)
    re.compile(r"fundamental\s+cycle", re.IGNORECASE),
    # 8. Governance rule definition in README.md
    re.compile(r"claims\s+of\s+perfection\s+or\s+universal", re.IGNORECASE),
]


def load_file_content(filename: str) -> Optional[str]:
    """Load text content of a dossier file, or None if not found."""
    path = DOSSIER_DIR / filename
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def strip_code_and_quotes(text: str) -> str:
    """Strip fenced code blocks and inline code to prevent false positives in regex scans."""
    # Strip markdown fenced code blocks: ```...```
    text_no_code = re.sub(r"```[\s\S]*?```", "", text)
    # Strip inline code: `...`
    text_no_inline = re.sub(r"`[^`\n]*`", "", text_no_code)
    return text_no_inline


# ==============================================================================
# TIER 1: Feature Coverage Tests
# ==============================================================================


class TestTier1FeatureCoverage:
    """Verifies that all required dossier files exist, are non-empty, and follow markdown structure."""

    def test_dossier_directory_exists(self) -> None:
        """Verify that docs/research_forum directory exists."""
        assert DOSSIER_DIR.exists(), f"Dossier directory does not exist at: {DOSSIER_DIR}"
        assert DOSSIER_DIR.is_dir(), f"Expected directory at: {DOSSIER_DIR}"

    @pytest.mark.parametrize("filename", EXPECTED_FILES)
    def test_file_exists(self, filename: str) -> None:
        """Verify that each of the 6 core deliverable files exists."""
        target = DOSSIER_DIR / filename
        assert target.is_file(), f"Missing required dossier file: {filename} (expected at {target})"

    @pytest.mark.parametrize("filename", EXPECTED_FILES)
    def test_file_substantial_content(self, filename: str) -> None:
        """Verify that each file has substantial technical content (> 1000 bytes and > 30 lines)."""
        target = DOSSIER_DIR / filename
        assert target.is_file(), f"File {filename} does not exist"
        size = target.stat().st_size
        assert size >= 1000, f"File {filename} is too short ({size} bytes, minimum expected 1000 bytes)"

        content = target.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        assert len(lines) >= 30, f"File {filename} has insufficient line count ({len(lines)} non-empty lines)"

    @pytest.mark.parametrize("filename", EXPECTED_FILES)
    def test_markdown_heading_hierarchy(self, filename: str) -> None:
        """Verify that each file contains a top-level H1 header and multiple structured H2/H3 sections."""
        target = DOSSIER_DIR / filename
        assert target.is_file(), f"File {filename} does not exist"
        content = target.read_text(encoding="utf-8")

        h1_matches = re.findall(r"^#\s+(.+)$", content, re.MULTILINE)
        assert len(h1_matches) >= 1, f"File {filename} must contain at least one H1 header ('# Title')"

        h2_matches = re.findall(r"^##\s+(.+)$", content, re.MULTILINE)
        assert len(h2_matches) >= 2, f"File {filename} must contain at least two H2 sections ('## Section')"


# ==============================================================================
# TIER 2: Boundary & Technical Scenarios Tests
# ==============================================================================


class TestTier2BoundaryScenarios:
    """Verifies detailed coverage of competitive systems, 5 boundary scenarios, 10-dimension matrix, 5 upgrades, and formal links."""

    # --------------------------------------------------------------------------
    # Competitive Systems Deep Dives (02_competitive_analysis.md)
    # --------------------------------------------------------------------------

    def test_competitive_systems_covered(self) -> None:
        """Verify that Readyset, Materialize, Noria, Redis/Dragonfly, and SimpleCache are deeply analyzed."""
        content = load_file_content("02_competitive_analysis.md")
        assert content is not None, "02_competitive_analysis.md is missing"

        # Check Readyset coverage
        assert re.search(r"Readyset", content, re.IGNORECASE), "Readyset analysis missing"
        assert re.search(r"up-quer(y|ies)", content, re.IGNORECASE), "Readyset up-query mechanism missing"
        assert re.search(r"Differential\s+Dataflow", content, re.IGNORECASE), "Readyset DD foundation missing"

        # Check Materialize coverage
        assert re.search(r"Materialize", content, re.IGNORECASE), "Materialize analysis missing"
        assert re.search(r"Timely(\s+Dataflow)?", content, re.IGNORECASE), "Materialize Timely Dataflow missing"
        assert re.search(r"arrangement(s)?", content, re.IGNORECASE), "Materialize operator arrangements missing"

        # Check Noria coverage
        assert re.search(r"Noria", content, re.IGNORECASE), "Noria analysis missing"
        assert re.search(r"partially[- ]evaluated", content, re.IGNORECASE), "Noria partial evaluation missing"
        assert re.search(r"(concurrency|race\s+condition|bi-directional)", content, re.IGNORECASE), "Noria concurrency failure modes missing"

        # Check Redis / Dragonfly coverage
        assert re.search(r"Redis", content, re.IGNORECASE), "Redis analysis missing"
        assert re.search(r"Dragonfly", content, re.IGNORECASE), "Dragonfly analysis missing"
        assert re.search(r"cache[- ]aside", content, re.IGNORECASE), "Redis cache-aside pattern missing"
        assert re.search(r"(thundering\s+herd|stampede)", content, re.IGNORECASE), "Redis cache stampede failure mode missing"

        # Check SimpleCache BQNF foundation
        assert re.search(r"SimpleCache", content, re.IGNORECASE), "SimpleCache analysis missing"
        assert re.search(r"BQNF|Boundary[- ]Quotient", content), "SimpleCache BQNF missing"
        assert re.search(r"Q\^1\s*=\s*C\^1\s*/\s*(im|operatorname\{im\})\(d_?0\)", content) or \
               re.search(r"quotient\s+obstruction", content, re.IGNORECASE), "SimpleCache quotient obstruction complex missing"

    # --------------------------------------------------------------------------
    # Five Operational Query Boundary Scenarios (02_competitive_analysis.md)
    # --------------------------------------------------------------------------

    def test_scenario_1_range_boundaries(self) -> None:
        """Scenario 1: Range boundaries, predicate containment, preserve vs repair vs invalidate."""
        content = load_file_content("02_competitive_analysis.md")
        assert content is not None, "02_competitive_analysis.md missing"

        assert re.search(r"Scenario\s+1.*Range", content, re.IGNORECASE), "Scenario 1 (Range Boundaries) missing"
        assert re.search(r"BETWEEN", content), "Range predicate example (BETWEEN) missing"
        assert re.search(r"preserve", content, re.IGNORECASE), "Preserve certification for outside boundary missing"
        assert re.search(r"signed\s+delta", content, re.IGNORECASE), "Signed delta repair for inside boundary missing"

    def test_scenario_2_filtered_aggregates(self) -> None:
        """Scenario 2: Filtered aggregates, group shifts, and missing evidence bounds."""
        content = load_file_content("02_competitive_analysis.md")
        assert content is not None, "02_competitive_analysis.md missing"

        assert re.search(r"Scenario\s+2.*(Filtered\s+Aggregate|Group)", content, re.IGNORECASE), "Scenario 2 missing"
        assert re.search(r"(SUM|COUNT|AVG)", content), "Filtered aggregate functions missing"
        assert re.search(r"(unrelated\s+column|tracking)", content, re.IGNORECASE), "Unrelated column preservation missing"
        assert re.search(r"(missing\s+evidence|missing_evidence_for_repair|incomplete\s+CDC)", content, re.IGNORECASE), "Missing evidence refusal missing"

    def test_scenario_3_non_invertible_extrema(self) -> None:
        """Scenario 3: Non-invertible extrema (MIN/MAX) monotonicity and priority-queue cochains."""
        content = load_file_content("02_competitive_analysis.md")
        assert content is not None, "02_competitive_analysis.md missing"

        assert re.search(r"Scenario\s+3.*(Extrema|MIN|MAX|Monotonicity)", content, re.IGNORECASE), "Scenario 3 missing"
        assert re.search(r"monotonic(ally)?", content, re.IGNORECASE), "Monotonic addition analysis missing"
        assert re.search(r"non[- ]destructive\s+delet(e|ion)", content, re.IGNORECASE), "Non-destructive deletion analysis missing"
        assert re.search(r"destructive\s+delet(e|ion)", content, re.IGNORECASE), "Destructive deletion analysis missing"
        assert re.search(r"cochain|priority[- ]queue", content, re.IGNORECASE), "Extrema cochain / priority queue mechanism missing"

    def test_scenario_4_dynamic_dyadic_quantiles(self) -> None:
        """Scenario 4: Dynamic dyadic-bucket quantile sketches under write bursts."""
        content = load_file_content("02_competitive_analysis.md")
        assert content is not None, "02_competitive_analysis.md missing"

        assert re.search(r"Scenario\s+4.*(Quantile|Dyadic)", content, re.IGNORECASE), "Scenario 4 missing"
        assert re.search(r"dyadic", content, re.IGNORECASE), "Dyadic buckets missing"
        assert re.search(r"(epsilon|\\epsilon|\u03b5)[- ](BQNF|tolerance)?", content, re.IGNORECASE), "Epsilon tolerance bound missing"
        assert re.search(r"(split|merge)", content, re.IGNORECASE), "Dyadic bucket split/merge adaptation missing"

    def test_scenario_5_out_of_order_freshness_posets(self) -> None:
        """Scenario 5: Out-of-order CDC streams, event-time watermarking, and observer freshness posets."""
        content = load_file_content("02_competitive_analysis.md")
        assert content is not None, "02_competitive_analysis.md missing"

        assert re.search(r"Scenario\s+5.*(Out[- ]of[- ]Order|Watermark|Freshness)", content, re.IGNORECASE), "Scenario 5 missing"
        assert re.search(r"watermark", content, re.IGNORECASE), "Watermark specification missing"
        assert re.search(r"freshness\s+poset", content, re.IGNORECASE), "Freshness poset missing"
        assert re.search(r"(LagExceeded|replica_lag|SLA)", content, re.IGNORECASE), "Lag SLA refusal missing"

    # --------------------------------------------------------------------------
    # 10-Dimension Architectural Trade-Off Matrix (02_competitive_analysis.md)
    # --------------------------------------------------------------------------

    def test_architectural_tradeoff_matrix(self) -> None:
        """Verify that the 10-dimension architectural trade-off matrix is present and complete."""
        content = load_file_content("02_competitive_analysis.md")
        assert content is not None, "02_competitive_analysis.md missing"

        required_dimensions = [
            r"Invalidation\s+Mechanism",
            r"Decision\s+Granularity",
            r"Evidence\s+Requirements",
            r"Memory\s+Overhead",
            r"Intermediate\s+State",
            r"SQL\s+Fragment\s+Support",
            r"Freshness\s+Semantics",
            r"Write\s+Churn\s+Resilience",
            r"Failover\s+(&|and)\s+Recovery",
            r"Transparency\s+(&|and)\s+Audit",
        ]

        for dim_pattern in required_dimensions:
            assert re.search(dim_pattern, content, re.IGNORECASE), f"Trade-off matrix missing dimension: {dim_pattern}"

        # Verify all 5 systems are included in the comparison table
        table_systems = ["Readyset", "Materialize", "Noria", "Redis", "SimpleCache"]
        for sys in table_systems:
            assert re.search(sys, content), f"Trade-off matrix table missing column/row for system: {sys}"

    # --------------------------------------------------------------------------
    # Five Commercial Upgrades (03_commercial_upgrades.md)
    # --------------------------------------------------------------------------

    def test_five_commercial_upgrades_present(self) -> None:
        """Verify that all five commercial upgrades are specified with dedicated sections."""
        content = load_file_content("03_commercial_upgrades.md")
        assert content is not None, "03_commercial_upgrades.md missing"

        upgrades = [
            (1, r"Multi-Tenant(\s+Observer-Relative)?\s+Cache\s+Isolation"),
            (2, r"Online\s+Zero-Downtime\s+Schema\s+Evolution"),
            (3, r"Distributed\s+Cross-Partition\s+Federated\s+CDC"),
            (4, r"Regulatory\s+Compliance\s+Audit\s+Trail"),
            (5, r"Watermarked\s+Out-of-Order\s+Stream\s+Reconciliation"),
        ]

        for num, pattern in upgrades:
            assert re.search(rf"Upgrade\s+{num}.*{pattern}", content, re.IGNORECASE), f"Commercial Upgrade {num} missing: {pattern}"

    def test_commercial_upgrades_technical_extensions(self) -> None:
        """Verify that each upgrade details technical extensions to parser, boundary tester, or certificate engine."""
        content = load_file_content("03_commercial_upgrades.md")
        assert content is not None, "03_commercial_upgrades.md missing"

        # Check references to parser / extractor
        assert re.search(r"extractor\.py|Query\s+Parser|AST", content, re.IGNORECASE), "Parser/AST extensions missing"
        # Check references to boundary tester / join engine
        assert re.search(r"join\.py|smplcache\.py|Boundary\s+(Diagnostic|Tester|Engine)", content, re.IGNORECASE), "Boundary engine extensions missing"
        # Check references to certificate / receipt engine
        assert re.search(r"certificate|receipt|merkle|TraceMerkleReceipt", content, re.IGNORECASE), "Certificate/receipt extensions missing"

    def test_commercial_upgrades_pricing_and_monetization(self) -> None:
        """Verify that commercial upgrades include enterprise ACV pricing ($25k - $350k)."""
        content = load_file_content("03_commercial_upgrades.md")
        assert content is not None, "03_commercial_upgrades.md missing"

        assert re.search(r"\$(25|50|60|75|100|200|250|300|350),?000|\$25k|\$350k", content, re.IGNORECASE), \
            "Enterprise ACV pricing figures missing in 03_commercial_upgrades.md"
        assert re.search(r"ACV|Annual\s+Contract\s+Value|monetiz(e|ation)", content, re.IGNORECASE), \
            "Commercial monetization terminology missing"

    # --------------------------------------------------------------------------
    # Cross-Program Foundations Linkages (04_cross_program_foundations.md)
    # --------------------------------------------------------------------------

    def test_cross_program_foundations_linkage(self) -> None:
        """Verify formal mappings to Genesis, WDP, LgGR, and simplex-normal-forms."""
        content = load_file_content("04_cross_program_foundations.md")
        assert content is not None, "04_cross_program_foundations.md missing"

        # Genesis Program linkages
        assert re.search(r"Genesis(\s+Program)?", content, re.IGNORECASE), "Genesis program missing"
        assert re.search(r"d_?1.*d_?0\s*=\s*0|nilpoten(t|cy)", content, re.IGNORECASE), "Genesis boundary nilpotency missing"
        assert re.search(r"AuthorityEffect:\s*None", content), "AuthorityEffect: None missing"

        # WDP Program linkages
        assert re.search(r"World\s+Description\s+Protocol|WDP", content), "WDP program missing"
        assert re.search(r"WDNF|Witnessed\s+Difference", content), "WDNF normal form missing"

        # LgGR Program linkages
        assert re.search(r"LgGR|Local\s+Green,\s+Global\s+Red", content, re.IGNORECASE), "LgGR program missing"
        assert re.search(r"StreamingCochainMonitor|StreamingFiberMonitor", content), "LgGR monitors missing"
        assert re.search(r"\\partial_\{?\\text\{?comm\}?\}?|commutator\s+boundary", content, re.IGNORECASE), "Commutator boundary missing"

        # simplex-normal-forms linkages
        assert re.search(r"simplex[- ]normal[- ]forms", content, re.IGNORECASE), "simplex-normal-forms missing"
        assert re.search(r"112\s+normal\s+form", content, re.IGNORECASE), "112 normal forms count missing"
        assert re.search(r"BQNF|Boundary[- ]Quotient\s+Normal\s+Form", content), "BQNF missing"
        assert re.search(r"SERNF|Split\s+Extension[- ]Retraction", content), "SERNF missing"
        assert re.search(r"TMNF|Trace\s+Merkle", content), "TMNF missing"
        assert re.search(r"FNF|freshness\s+poset|epistemic\.freshness", content, re.IGNORECASE), "FNF freshness poset missing"
        assert re.search(r"CODA_MUR", content), "CODA_MUR representation transport missing"
        assert re.search(r"LossLedger(s)?", content), "LossLedger missing"


# ==============================================================================
# TIER 3: Cross-Feature Consistency Tests
# ==============================================================================


class TestTier3CrossFeatureConsistency:
    """Verifies that all 6 dossier documents form a consistent, cross-referenced research forum."""

    def test_competitive_to_upgrades_cross_referencing(self) -> None:
        """Verify that commercial upgrades address specific limitations of Readyset, Materialize, Noria, and Redis."""
        content_upgrades = load_file_content("03_commercial_upgrades.md")
        assert content_upgrades is not None, "03_commercial_upgrades.md missing"

        for competitor in ["Readyset", "Materialize", "Noria", "Redis"]:
            assert re.search(competitor, content_upgrades), f"03_commercial_upgrades.md must cross-reference {competitor}"

    def test_upgrades_to_foundations_mapping(self) -> None:
        """Verify that 03_commercial_upgrades.md cites specific formal theorems and contracts from foundations."""
        content_upgrades = load_file_content("03_commercial_upgrades.md")
        assert content_upgrades is not None, "03_commercial_upgrades.md missing"

        # Check references to Theorems
        assert re.search(r"Theorem\s+[0-9]+", content_upgrades), "Theorems must be cited in 03_commercial_upgrades.md"
        # Check references to normal form contracts
        assert re.search(r"nf\.[a-z0-9_.]+", content_upgrades), "Normal form YAML contract IDs must be cited in 03_commercial_upgrades.md"

    def test_whitepaper_to_upgrades_and_economics(self) -> None:
        """Verify that 01_executive_whitepaper.md synthesizes the 5 upgrades, TCO, and financial ROI."""
        content_whitepaper = load_file_content("01_executive_whitepaper.md")
        assert content_whitepaper is not None, "01_executive_whitepaper.md missing"

        assert re.search(r"five\s+commercial\s+upgrades|commercial\s+upgrades", content_whitepaper, re.IGNORECASE), \
            "Whitepaper must reference the commercial upgrades"
        assert re.search(r"TCO|Total\s+Cost\s+of\s+Ownership", content_whitepaper, re.IGNORECASE), \
            "Whitepaper must include TCO analysis"
        assert re.search(r"ROI|Return\s+on\s+Investment", content_whitepaper, re.IGNORECASE), \
            "Whitepaper must include ROI financial model"

    def test_proceedings_and_readme_cohesion(self) -> None:
        """Verify that README.md and 05_research_forum_proceedings.md tie the research forum together."""
        content_readme = load_file_content("README.md")
        assert content_readme is not None, "README.md missing"

        content_proceedings = load_file_content("05_research_forum_proceedings.md")
        assert content_proceedings is not None, "05_research_forum_proceedings.md missing"

        # README should index all 5 companion files
        for f in EXPECTED_FILES[1:]:
            assert f in content_readme, f"README.md must link to companion file {f}"

        # Proceedings should contain structured debate panels
        assert re.search(r"Panel|Debate|Proceedings|Dispute", content_proceedings, re.IGNORECASE), \
            "05_research_forum_proceedings.md must contain structured debate panels"


# ==============================================================================
# TIER 4: Claim Hygiene Enforcement Tests
# ==============================================================================


class TestTier4ClaimHygiene:
    """Enforces strict epistemic claim hygiene across all files in docs/research_forum/."""

    @pytest.mark.parametrize("filename", EXPECTED_FILES)
    def test_forbidden_words_scanner(self, filename: str) -> None:
        """Scan file for forbidden words; fail if any unauthorized occurrences are found."""
        path = DOSSIER_DIR / filename
        assert path.is_file(), f"File {filename} does not exist"
        raw_content = path.read_text(encoding="utf-8")

        lines = raw_content.splitlines()
        violations: List[str] = []

        in_fenced_code = False
        in_claim_hygiene_rules_block = False

        for line_idx, line in enumerate(lines, 1):
            stripped = line.strip()

            # Track fenced code blocks
            if stripped.startswith("```"):
                in_fenced_code = not in_fenced_code
                continue
            if in_fenced_code:
                continue

            # Skip lines that are defining the rule itself or replacement tables (bounded to rule heading)
            if re.search(r"Claim\s+Hygiene\s+Rule|Forbidden\s+words|Replacement\s+defaults", line, re.IGNORECASE):
                in_claim_hygiene_rules_block = True
                continue
            if in_claim_hygiene_rules_block:
                if stripped.startswith("#"):
                    in_claim_hygiene_rules_block = False
                elif re.search(r"^\s*-\s*[\"“]?(?:proves|guarantees|fundamentally|impossible|universal|flawless|perfectly|clearly|obviously|simply|merely|weaponized)", line, re.IGNORECASE):
                    continue
                elif re.search(r"proves\s*→|guarantees\s*→|fundamental\s*→", line, re.IGNORECASE):
                    continue

            # Strip inline code from line before word check
            line_clean = re.sub(r"`[^`]*`", "", line)

            # Check each forbidden pattern
            for label, pattern in FORBIDDEN_PATTERNS:
                match = pattern.search(line_clean)
                if match:
                    # Check if this match falls under an approved contextual phrase
                    matched_phrase = False
                    for app in APPROVED_PHRASES:
                        if app.search(line_clean):
                            matched_phrase = True
                            break

                    if not matched_phrase:
                        violations.append(
                            f"{filename}:{line_idx} - Forbidden word/stem '{match.group(0)}' [{label}] found in: \"{stripped}\""
                        )

        assert not violations, (
            f"Found {len(violations)} claim hygiene violation(s) in {filename}:\n"
            + "\n".join(violations[:15])
            + (f"\n... and {len(violations) - 15} more" if len(violations) > 15 else "")
        )

    @pytest.mark.parametrize("filename", EXPECTED_FILES)
    def test_four_point_claim_audit_present(self, filename: str) -> None:
        """Verify that every deliverable contains the mandatory 4-point epistemic claim audit."""
        path = DOSSIER_DIR / filename
        assert path.is_file(), f"File {filename} does not exist"
        content = path.read_text(encoding="utf-8")

        # Point 1: What it supports
        p1 = re.search(r"What\s+(it|this\s+dossier|this\s+report|this\s+analysis)\s+supports", content, re.IGNORECASE)
        assert p1, f"File {filename} missing Claim Audit Point 1: 'What it supports'"

        # Point 2: What it does not support
        p2 = re.search(r"What\s+(it|this\s+dossier|this\s+report|this\s+analysis)\s+does\s+not\s+support", content, re.IGNORECASE)
        assert p2, f"File {filename} missing Claim Audit Point 2: 'What it does not support'"

        # Point 3: The narrowest defensible claim
        p3 = re.search(r"(The\s+)?narrowest\s+defensible\s+claim", content, re.IGNORECASE)
        assert p3, f"File {filename} missing Claim Audit Point 3: 'The narrowest defensible claim'"

        # Point 4: The next test needed
        p4 = re.search(r"(The\s+)?next\s+test\s+needed", content, re.IGNORECASE)
        assert p4, f"File {filename} missing Claim Audit Point 4: 'The next test needed'"

    def test_hardened_scanner_mutation_sensitivity(self) -> None:
        """Verify that the scanner catches singular, past-tense, and stem variations of forbidden words."""
        synthetic_violations = [
            "We guarantee high throughput.",
            "This proves our system works.",
            "The engineer proved the fix in testing.",
            "This is a fundamental limitation of databases.",
            "It is impossible to miss an update.",
            "Universal SQL coverage is provided.",
            "The execution is flawless.",
            "The data converges perfectly.",
            "Clearly, this design is optimal.",
            "Obviously, the cache is faster.",
            "Simply update the row.",
            "It merely invalidates the key.",
            "Attackers weaponized the cache.",
            "Theorem 7 — Aggregate Repair Completeness (Proved in Kernel)",
            "Theorem 8 — Topological Workload Decomposition (Proved in CLI)",
        ]
        for sentence in synthetic_violations:
            violation_found = False
            for label, pattern in FORBIDDEN_PATTERNS:
                if pattern.search(sentence):
                    if not any(app.search(sentence) for app in APPROVED_PHRASES):
                        violation_found = True
                        break
            assert violation_found, f"Hardened scanner failed to catch synthetic violation: '{sentence}'"

