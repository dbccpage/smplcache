# 06: Operator Contract and Cohomological Descent

This document formalizes the role of **Operators** within the UROS Ascent Tower and the Omega Engine. It bridges the rigid contract definitions of the `OperatorIdentity` with the mathematical requirements of cohomological descent and structural repair.

---

## 1. The Mathematical Nature of Operators

The `OperatorIdentity` contract enforces a severe restriction on what an operator is allowed to be:

> Operators are restricted to be **structure-preserving morphisms**, not solvers.

Formally, operators in the Omega Engine are:
*   **Deterministic** endomorphisms or homomorphisms on carriers.
*   **Stateless** (no dependence on history or hidden state).
*   **Non-searching** (no branching, no convergence loops).
*   **Explicit about carrier change**.
*   **Certified against monotone functionals** ($\Phi$, $\chi$, $\Gamma_2$).

Mathematically, this means:
**Operators are elements of a finite, typed, algebraic rewrite system, not a computational search space.**

## 1.1 What is and is not an Operator

> **Operator:** changes or proposes state structure.
> **Functional:** measures state structure.
> **Evaluator:** judges measured facts against policy.

Do **not** include Functionals or Evaluators in the operator space. Functionals measure; Evaluators judge. If they become Operators, judgment flows downward into state transformation, violating the global architecture.

| Thing | Should be Operator? | Reason |
| --- | --- | --- |
| Lambda | yes | transforms carrier/state |
| Reducer | yes | canonicalizes same carrier |
| Transform | yes | rearranges same carrier |
| Encoder | yes-ish | maps into constrained representation |
| Generator | yes-ish | emits detached candidates |
| Constructor | yes-ish | creates BaseType from raw input |
| Functional | **no** | computes measurements |
| Evaluator | **no** | emits admissibility verdicts |
| Solver | **no** | computes mathematical facts |
| Analysis | **no** | verifies facts |
| Diagnostic | **no** | reports health |

This places the Omega Engine firmly in the domain of rewrite theory, monotone dynamical systems, cohomological descent, and well-founded measures. This is the *only* regime where the central conjecture of the Omega Engine makes sense.

---

## 2. Cohomology and Finiteness: Why Operator Sets are Plausible

It is a core architectural claim that the operator lattice at any given level $N$ is finite. This is not an arbitrary assumption; it is a consequence of how operators interact with obstructions.

> **Operators act on obstruction classes, not on raw data.**

Because obstruction equivalence quotients the raw space, most degrees of freedom simply *do not exist* at level $N$. The Engine operates on **obstruction-relevant normal forms** (equivalence classes under admissible repair).

This is exactly why **$\ell^1$ cohomology** matters. $\ell^1$ cohomology does two critical things:
1.  **Localizes obstruction support:** Obstructions concentrate on minimal cycles/boundaries.
2.  **Forbids cancellation and diffusion:** Independent failures do not smear into an exponential search space.

**Conclusion:** The space of *distinct obstruction shapes* at level $N$ is finite (or at worst locally finite). This is the mathematical reason the operator lattice can be finite without cheating.

---

## 3. The Three Morphism Counts

In this rewrite system, it is crucial not to confuse three different counts of operators:

1. **Primitive Morphism Kinds**: Finite and small (e.g., ~15 kinds like Lambda, Reducer, Encoder, Projector, etc.). These dictate the structural algebra.
2. **Registered Morphism Instances**: Finite by Codex version (e.g., 420+ domain-specialized instances like `reducer_h1`, `encoder_density`). These exist at specific heights in the operator algebra.
3. **Composite Morphisms**: Syntactically infinite (e.g., $f$, $g \circ f$, $h \circ g \circ f$), but **finite per MSS level after quotienting** by obstruction behavior (same source/target, same trace-normal form, same admissible context behavior).

To prevent confusing the 15 kinds with the 420 instances, an operator identity is typed distinctly:

```rust
struct MorphismId {
    kind: MorphismKind,
    source: SchemaRef,
    target: SchemaRef,
    height: OperatorHeight,
    domain: DomainId,
    license: Option<LicenseId>,
}
```

---

## 4. Non-Explosive Search

The Omega Engine does not claim polynomial-time solving of arbitrary problems. Instead, it claims something much more precise:

> **The engine never explores combinatorial alternatives inside operators. All branching lives at the Engine level, not the Operator level.**

This prevents combinatorial explosion because:
*   Operators are *functions*, not choices.
*   Each operator application either strictly lowers $\Phi$, leaves $\Phi$ unchanged but lowers $\Gamma_2$, or fails deterministically.
*   There is no internal backtracking inside an operator.

Explosion is prevented by explicit contract invariants: idempotence (`Reducers`), $\Phi$-monotonicity (`StrictLambda`), carrier-discipline (no hidden lifts), and exhaustion detection (`MetaLift`). The search tree is exponential only in *obstruction depth*, which is strictly controlled by the MSS-$N$ hierarchy.

---

## 5. The Three Theorems of Convergence

The convergence of the UROS / MSS tower reduces to three critical theorems.

### Theorem A: Finite Obstruction Basis per Level
> For each MSS-$N$, there exists a finite (or compactly generated) basis of obstruction equivalence classes under admissible contexts.

This is a **cohomological finiteness** statement (e.g., finite cycle bases in graphs, finite critical pairs in syntax). This must be proven per level.

### Theorem B: Well-Founded Descent Measure
> The lexicographic tuple $(\Phi, \Gamma_2, \Xi, \dots)$ is well-founded under admissible operators.

There can be no infinite descending chains and no infinite plateaus with admissible internal moves. This guarantees that internal exhaustion is detectable and that `MetaLift` is forced finitely many times per level.

### Theorem C: Lift Restores Congruence
> Every `MetaLiftOp` restores congruence of obstruction equivalence at the next level.

If a lift adds symbols but does not make obstruction equivalence a congruence, the engine could oscillate forever. The $\Delta \in \{1,2\}$ rule enforces minimality, but we also require **Lift Completeness**: every non-congruent obstruction is repaired by the forced lift.

---

## 6. Partial Decision and Honest Refusal

If any of the three theorems above fail for a specific domain, the framework is still *correct*, but the engine is **not universal**—it becomes a **partial decision system**.

This means the engine will correctly output:
> *"This problem cannot be solved under the current admissible operators and lifts."*

This is the desired behavior for a reasoning machine that does not hallucinate. Either the tower converges, or it **honestly refuses**.

---

## 7. Contract Definitions and Kinds

The implementation of these mathematical ideals is codified in the **Operator Tower**, which formally distinguishes between operators acting *within* a mathematical structure (Kernel) and operators acting *across* representation boundaries (Boundary).

### Critical Rule
*   If it changes executable state or structure, it is an **operator**.
*   If it only observes and emits facts, it is **Analysis**.
*   If it crosses the external/internal boundary, it is a **BoundaryOperator**.
*   If it changes meaning, it needs an explicit **lift/semantic license**.

### 7.1 Kernel Operators

Kernel operators are endomorphisms or structure-preserving morphisms that operate strictly *within* a validated `BaseType`.

*   **Lambda**: (`S -> T`) transforms carrier/state while preserving mathematical structure.
*   **Reducer**: (`S -> S`) canonicalizes the same carrier (idempotent, strictly complexity-reducing).
*   **Transform**: (`S -> S`) rearranges the same carrier (complexity-preserving, unrestrained $\Phi$).
*   **Projector**: (`BaseType -> BaseType` or `BaseType -> Substructure`) reduces view/dimension/coordinate scope.
*   **Normalizer**: (`BaseType -> BaseType`) canonicalizes representation, usually Reducer-like if idempotent.

### 7.2 Boundary Operators

Boundary operators handle the conversion, ingestion, and representation mapping between heterogeneous inputs and validated structures. They are **not** kernel operators.

*   **Adapter**: (`external/heterogeneous -> BaseType`) may change representation, but **must not create meaning**.
*   **Encoder**: (`S -> T`) maps state into a constrained or specialized representation.
*   **Decoder**: (`encoded representation -> BaseType`) maps from encoded back to BaseType, and **must preserve declared meaning**.
*   **Constructor**: (`Raw -> BaseType`) validates and instantiates typed structure from external/untrusted input.

### 7.3 The Reflector

*   **Reflector**: (`BaseType -> meta/structure artifact`) 
    *   *Note:* Use carefully. If it changes structure, it is a kernel transform. If it strictly observes and emits facts, it belongs under Analysis, not the Operator Kernel.

### The Meta Boundary
The `MetaLiftOp` (or `LiftOp`) is **not** a normal operator. It is a **Meta-governed structural extension**. It is a bridge between the operator tower and the Meta governance layer, invoked *only* upon operator exhaustion to explicitly extend the carrier. It does not belong in the standard operator tower membership.

**MetaLift Laws:**
1. **Explicitness** — $P \to P'$ is declared and witnessed.
2. **Repair-Enabling** — Restores confluence.
3. **Minimality** — $\Delta \in \{1,2\}$ constructors added.
4. **Context Dependency** — Forced only when $C[\square]$ distinguishes irreducible failure.
5. **No Hidden Lift** — Coercion is forbidden.


---

## 8. Operator Creation and Composability

Operators are the admissible functions defined strictly over a finite obstruction quotient. Their creation and composition follow strict algebraic laws bound to the Master Theory:

1. **Creation (Admissibility Bounds):** When a new operator is created, it cannot invent new observational power. It is an **Admissible Context** $C[\square]$ meaning it must preserve both well-formedness and repair monotonicity. It cannot act on "raw data", it only acts on the obstruction signature of the carrier.
2. **Composability (Closure):** The composition of operators $f \circ g$ is valid if and only if the composition forms a valid admissible context. Because nested admissible contexts do not increase arity (they remain binary-generating), operator composition cannot "accidentally" spawn a higher-arity $\Delta > 2$ lift requirement.
3. **Mass Discipline:** Operators must explicitly declare which mass they affect: **Obstruction mass** ($\|h\|_1$), **Area-lock mass** ($m(\gamma)$), or **Physical mass**. A Lambda operator may reduce obstruction mass by projecting to a cycle, but a Transform strictly preserves it.

---

## 9. Kolmogorov Compression ($\kappa$) and Area-Lock

Within the Operator Tower, the **Reducer** and **Transform** kinds are bound by the complexity functional $\kappa(s)$, defined as the Kolmogorov Compression of the state structure. This directly ties back to Area-Lock physics:

* **Area-Lock Filling Cost:** $m_G(\gamma) = \inf_{\partial F=\gamma}\|F\|_1$. The cost of removing an obstruction is the minimal active-face area $F$ required to fill it. 
* **Reducers ($\kappa(R(s)) \le \kappa(s)$):** Reducers computationally map to *gauge-removable fillings* ($m_G(\gamma) = 0$). They compress the Kolmogorov description of the state by executing zero-cost gauge retractions to eliminate pseudo-obstructions. They strictly decrease structural complexity without violating the irreducible Area-Lock boundary.
* **Transforms ($\kappa(T(s)) = \kappa(s)$):** Transforms alter the topological representation (e.g., basis rotations) without collapsing the structure. They must preserve the true area-lock mass ($m_G(\gamma) > 0$). They cannot compress the state because the irreducible obstruction has an absolute minimal $\ell^1$ filling cost that resists reduction.

Therefore, Kolmogorov Compression in the UROS engine is simply the computational execution of zero-cost gauge retractions. Once an operator hits the true Area-Lock boundary ($m_G(\gamma) > 0$), $\kappa$ bottoms out, the Reducer stalls, and the Engine is forced into a MetaLift.

---

## 10. Operator Evaluation Law

Operators do not evaluate themselves. They only propose structural transformations. The Engine executes a strict, 8-step lexicographic descent loop:
1. **Compute residual:** The engine calculates $h = \omega - d_0 f^*$.
2. **Stop if zero:** If $\Phi(h)=0$, the state is resolved.
3. **Exhaust internal updates:** Operators apply internal admissible repairs to reduce $\Phi$.
4. **Generate candidates:** If operators stall, the engine generates licensed local lift candidates.
5. **Optimize post-lift:** Perform post-lift optimization subject to evidence preservation.
6. **Accept strict descent:** Accept only operator applications or lifts that yield strict $\Phi$ descent.
7. **Minimize cost:** Choose the accepted candidate minimizing $\Phi_{\text{after}} + \lambda|\Lambda|$.
8. **Fail honestly:** If no operator or lift succeeds, return a typed failure certificate.

---

## 11. Operator Terminal State Contract

When the engine halts, it must rigidly type the terminal state to separate **mathematical refusal** from **resource-limited stopping**. This prevents time pressure from becoming ontology (e.g., silently turning "I stopped looking" into "there is no solution").

### The Four Terminal States

1. **Exact (`Exact`)**: \(\Phi_1(q)=0\). The obstruction is exact/gauge-removable/local-repairable. This means resolved.
2. **Stabilized Survivor (`StabilizedSurvivor`)**: \(\Phi_1(q)>0\), \(\Gamma_2(q)=0\). The obstruction does not vanish, but it is closed. It has become structure.
3. **Certified Exhaustion / Refusal (`CertifiedExhaustion`)**: \(\Phi_1(q)>0\), \(\Gamma_2(q)>0\), and the system has a valid exhaustion certificate \(\mathsf{Exh}_{L_n}(q)\). Meaning: every admissible operator/composition/lift route in the declared tier failed under certified evaluation. This is honest refusal.
4. **Budget Unknown (`BudgetUnknown`)**: The system stopped because a budget (time, search, memory, patience, solver) ran out before certified exhaustion. Therefore, \(\text{not solved} \neq \text{unsolvable} \neq \text{irreducible} \neq \text{survivor}\).

### The Canonical Law

$$
\boxed{
\mathsf{CertifiedExhaustion} \text{ requires proof of exhausted structure;} \quad \mathsf{BudgetUnknown} \text{ requires only exhausted resources.}
}
$$

### Implementation (Evaluator Rule)

```rust
enum TerminalState {
    Exact,
    StabilizedSurvivor,
    CertifiedExhaustion,
    BudgetUnknown,
}

// Evaluator rule for terminal packet classification:
match packet {
    trusted if phi == 0 => Exact,
    trusted if phi > 0 && gamma2 == 0 => StabilizedSurvivor,
    trusted if phi > 0 && gamma2 > 0 && exhaustion_certified => CertifiedExhaustion,
    _ if budget_exhausted && !exhaustion_certified => BudgetUnknown,
    _ => BudgetUnknown,
}
```

---

## 12. The Philosophical Punchline

> **The universe does not solve problems by search; it solves them by structure, and when structure is insufficient, it changes structure.**

The Omega Engine enforces this discipline computationally. We do not need to prove "it solves everything." We need to prove:
1. It never lies.
2. It never hides failure.
3. It never explodes spuriously.
4. Every lift is forced, minimal, and repair-enabling.
5. Every operator is local, finite, certified, and bounded by area-lock compression limits.
