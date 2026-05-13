# Quotient Repairability — The Theory Behind Smpl

> **Status:** Working theory document. Claims are scoped and testable.
> **Scope:** Finite workload-schema-CDC coupling, quotient obstruction, certified repair, normal-form projection, write-path certification.
> **Boundary:** This theory does not replace relational algebra. It solves a different runtime problem. SmplCache is the wedge, not the destination.

---

## Core Thesis

$$
\boxed{
\text{Relational theory normalizes data at rest. SmplCache certifies repairability of data in motion.}
}
$$

Codd's 1970 relational model was explicitly about logical data independence: users should not need to know internal representation, and applications should survive changes in representation. That goal stands. SmplCache does not contest it.

SmplCache answers a question Codd's framework does not address:

$$
\text{Given this workload and this update stream, which cached facts can be repaired without recomputation?}
$$

The product language is:

$$
\boxed{
\text{Don't invalidate what lies in } \operatorname{im}(d_0).
\quad
\text{Invalidate only the quotient obstruction.}
}
$$

Or equivalently:

$$
\boxed{
\text{Every cache decision carries a reason: repaired because exact, invalidated because obstructed.}
}
$$

That is stronger than ordinary invalidation heuristics.

---

## Part I — Core Theorems (Proved)

### Theorem 1 — Workload Repairability Theorem

Let $W$ be a finite workload of cached query shapes, and let $E$ be a finite class of CDC update events.

Define:

$$
C^1 = \mathbb{R}^D
$$

where $D$ is the finite set of cache-relevant distinctions: touched table, touched column, predicate bucket, group key, aggregate target, join edge, and required before/after value.

Let $C^0$ be the finite vector space of declared repair programs:

$$
\text{increment SUM}, \quad
\text{increment COUNT}, \quad
\text{delete group contribution}, \quad
\text{move contribution between groups}.
$$

Let

$$
d_0 : C^0 \to C^1
$$

map each repair program to the distinction-change it can absorb.

For an update event $e$, let

$$
h(e) \in C^1
$$

be the induced cache defect. Define

$$
Q^1 = C^1 / \operatorname{im}(d_0).
$$

Then:

$$
[h(e)] = 0 \in Q^1
$$

if and only if the event is repairable by the declared repair class.

If $[h(e)] \neq 0$, then no declared local repair can absorb the event; SmplCache must invalidate, refine, or lift the repair class.

#### Proof

By definition,

$$
[h(e)] = 0 \iff h(e) \in \operatorname{im}(d_0).
$$

This means there exists some repair vector $\alpha \in C^0$ such that $d_0 \alpha = h(e)$. That is exactly: a declared repair program produces the required cache delta.

Conversely, if no such $\alpha$ exists, then $h(e) \notin \operatorname{im}(d_0)$, so $[h(e)] \neq 0$, and the update is not repairable inside the declared repair class. $\square$

This is the core theorem for SmplCache.

---

### Theorem 2 — Static Normalization Is Not Cache Repairability

There exist schemas that violate classical normal forms but whose cached workload is repairable. There also exist highly normalized schemas whose cached workload is not repairable under the declared CDC evidence.

#### Example A — Non-normalized but cache-safe

Suppose a table contains:

```
orders(customer_id, customer_name, amount)
```

with dependency $\texttt{customer\_id} \to \texttt{customer\_name}$.

This creates a transitive/redundancy issue depending on the full schema design.

But suppose the cached query is:

```sql
SELECT customer_id, SUM(amount)
FROM orders
GROUP BY customer_id;
```

A CDC update changing only `customer_name` does not affect the cache result. Relative to this workload, the defect vector has no component in the query-sensitive coordinates. Therefore $[h(e)] = 0$ for that cache shape. The schema may be bad relational design, but the cache event is safe.

#### Example B — Normalized but cache-obstructed

Now take a clean normalized schema with separate `orders`, `line_items`, `customers`.

Suppose the cached query joins across tables and groups by a derived customer segment. If a CDC event changes a join key or predicate membership, and the event lacks the old value needed to subtract the previous contribution, then the update may not be repairable.

The schema can be normalized, but the cache repair class lacks the required evidence. Then $[h(e)] \neq 0$.

So normalization does not imply cache repairability. $\square$

**This theorem is important.** It prevents the pitch from becoming "QNF replaces normal forms." It does not. It solves a different runtime problem.

Schema normal form is not decisive for cache repairability; repairability is a property of the workload, the CDC evidence, and the declared repair class.

---

### Theorem 3 — Normal-Form Projection Theorem

Given a relational schema $R$ with declared dependencies $D$, construct labeled subcomplexes:

$$
C^\bullet_{\mathrm{FD}}, \quad
C^\bullet_{\mathrm{MVD}}, \quad
C^\bullet_{\mathrm{JD}}, \quad
C^\bullet_{\mathrm{Workload}}.
$$

Each subcomplex has a quotient:

$$
Q^1_\tau = C^1_\tau / \operatorname{im}(d_{0,\tau})
$$

for dependency type $\tau$.

Then a classical normal-form violation corresponds to a nonzero class in the appropriate labeled quotient:

| Violation | Labeled quotient |
|---|---|
| 2NF violation | $q \in Q^1_{\mathrm{partial\text{-}key}}$ |
| 3NF violation | $q \in Q^1_{\mathrm{transitive}}$ |
| BCNF violation | $q \in Q^1_{\mathrm{non\text{-}superkey\text{-}det}}$ |
| 4NF violation | $q \in Q^1_{\mathrm{MVD}}$ |
| 5NF/6NF violation | $q \in Q^1_{\mathrm{JD}}$ |

Fagin's 1977 work introduced multivalued dependencies and 4NF as a strengthening beyond BCNF, with lossless decomposition into 4NF schemas. 6NF is usually stated in terms of having no nontrivial join dependencies, especially in Date-style temporal/interval settings.

**The theorem is one-way until the exact matrices are built:**

$$
\boxed{
\text{known violation} \Rightarrow \text{nonzero labeled obstruction class.}
}
$$

The stronger equivalence:

$$
\text{nonzero labeled obstruction class} \iff \text{normal-form violation}
$$

requires a fully specified construction for each dependency type. That is the next formal test.

#### Safe phrasing

Given a dependency-specific cochain encoding, a normal-form violation is represented by a nonzero class in the corresponding labeled quotient obstruction space.

If every dependency-specific labeled quotient vanishes under the chosen encoding, then the encoded dependency violations vanish.

---

### Theorem 4 — Context Witness = Cache Lift Trigger

Let $p$ be the cache profile map:

$$
p : \text{database states} \to Q^1_{\mathrm{cache}}.
$$

Let $c$ be an admissible event context (CDC update, insert, delete, key move, predicate membership change).

Define:

$$
x \sim y \iff p(x) = p(y).
$$

If there exist states $x, y$ such that $p(x) = p(y)$ but $p(cx) \neq p(cy)$, then $w = (x, y, c)$ is a **cache context witness**.

This means the current cache profile is too coarse: it identifies states that behave differently under an admissible update.

Therefore SmplCache must perform one of:

- **lift:** add evidence/sensitivity
- **refine:** split the cache shape
- **invalidate:** refuse local repair
- **ask for stronger CDC:** old values, predicate columns, join keys

#### Proof

The current profile says $x$ and $y$ are equivalent: $p(x) = p(y)$.

But after the event context: $p(cx) \neq p(cy)$.

So equivalence is not stable under context. That is exactly failure of contextual congruence.

A repair policy based on the old profile cannot be sound, because it would apply the same treatment to $x$ and $y$, while the event requires different treatments.

Therefore the system must refine, lift, or refuse. $\square$

---

### Theorem 5 — Boundary-Certified Write Path Theorem

Given a write event $e$, every downstream system component $L_i$ has:

$$
C^0_i \xrightarrow{d_{0,i}} C^1_i
$$

where:

- $C^1_i$ is the space of distinctions that layer can observe;
- $\operatorname{im}(d_{0,i})$ is the space of changes it can repair exactly;
- $Q^1_i = C^1_i / \operatorname{im}(d_{0,i})$ is the layer's irreducible obstruction space.

Then the write is locally absorbable at layer $L_i$ iff:

$$
[h_i(e)] = 0 \in Q^1_i.
$$

If $[h_i(e)] \neq 0$, then that layer cannot honestly claim local repair. It must invalidate, lift, escalate, or refuse.

#### Proof

This is a direct application of Theorem 1 to each layer independently. Each layer has its own repair class $C^0_i$ and its own observable distinctions $C^1_i$. The quotient test is local: it does not require global knowledge. The layer either contains the event in its image or it does not. $\square$

#### Consequence

$$
\boxed{
\text{Every layer either repairs by certificate or admits obstruction.}
}
$$

---

## Part II — Strategic Goals

### The Broader Thesis

$$
\boxed{
\text{Modern data systems are overcomplicated because they route state changes through machinery that cannot certify repair.}
}
$$

Every layer panics defensively:

$$
\text{app} \to \text{ORM} \to \text{query planner} \to \text{cache} \to \text{WAL} \to \text{replication} \to \text{storage} \to \text{network} \to \text{analytics}
$$

Each layer sees a partial shadow of the event and over-invalidates, over-locks, over-recomputes, or over-logs because it cannot prove $h \in \operatorname{im}(d_0)$ — meaning: "this change is exactly repairable here."

### What We Kill and How

| Incumbent | What they do wrong | Our structural advantage |
|---|---|---|
| **Oracle/MS SQL** | Writes are untyped mutations. Cache coherence is lock-based guesswork. Materialized view refresh is full-scan or heuristic. | Every write produces a typed boundary $h(e) \in C^1$. Quotient test decides exact repair vs. invalidation with a certificate. No lock needed for read-coherence. |
| **PostgreSQL** | Logical replication emits unstructured CDC. Downstream consumers (PgCache, Debezium) parse WAL and guess what changed. | Boundary is computed at write time. CDC is exact, policy-masked, and shape-routed before it leaves the engine. |
| **MongoDB** | Change streams give you "something changed in this document." No structural decomposition of what changed relative to which queries. | Dependency fingerprints ($\text{DependencyFingerprint}$) decompose the write into per-shape sensitivity. Non-intersecting writes are provably invisible. |
| **Redis/Memcached** | Pure key-value. TTL expiry or manual invalidation. No concept of "this write does not affect this cache entry." | Quotient repairability replaces TTL with certified repair. Cache entries that lie in $\operatorname{im}(d_0)$ are repaired, not expired. |
| **DuckDB/ClickHouse** | Analytical engines. No incremental maintenance story. Full re-scan on mutation. | Live aggregates updated by boundary folding ($\texttt{UpdateAggregate}$). Aggregates are cochain evaluations, not re-scans. |
| **Debezium/Kafka CDC** | CDC is a dumb pipe. Consumer must figure out what the event means for each downstream shape. | Boundary routing (`RouteBoundary`) does shape-intersection at emit time. Consumer receives only relevant, pre-classified events. |

---

## Part III — Theorem Proofs and Gates

These proofs use a finite SmplCache model. A theorem is marked **Proved** only when the objects are explicitly finite and the realization scope is declared. A **conditional** theorem is mathematically valid under stated hypotheses, but still needs an implementation witness or a SQL realization witness before it becomes a product claim.

---

### Theorem 6 — Noncommutation Detection (Conditional; implementation open)

**Statement:** Let $U_A, U_B$ be two concurrent write operators on the same state. Define the commutator boundary:

$$
\partial_{\text{comm}}(A,B) = U_A \circ U_B - U_B \circ U_A.
$$

Let $\rho$ be the realization map from concrete state differences to the finite observer-visible distinction space $C^1$. The operational commutator boundary is:

$$
\partial_{\text{comm}}^\rho(A,B;x)
= \rho(U_AU_Bx - U_BU_Ax).
$$

Assume $\rho$ is faithful on the protected observer-visible conflict subspace:

$$
\rho(y-z)=0
\iff
y \text{ and } z \text{ are equivalent for all protected cache observers.}
$$

Then $\partial_{\text{comm}}^\rho(A,B;x)=0$ iff $U_A$ and $U_B$ commute at $x$ up to protected observer equivalence. If $\partial_{\text{comm}}^\rho(A,B;x)\neq 0$, the writes are not observer-commuting and the runtime must serialize, merge via a declared 2-cell, or reject with a conflict certificate.

**Why it matters:** This is the algebraic replacement for pessimistic locking. If commutation is certified structurally, many lock decisions can be replaced by boundary tests; otherwise the system falls back to serialization.

#### Proof

By definition, $\partial_{\text{comm}}^\rho(A,B;x)=0$ iff:

$$
\rho(U_AU_Bx - U_BU_Ax)=0.
$$

By faithfulness of $\rho$ on the protected conflict subspace, this holds iff the two candidate final states $U_AU_Bx$ and $U_BU_Ax$ are equivalent for every protected observer. That is exactly observer-relative commutation.

Conversely, if $\partial_{\text{comm}}^\rho(A,B;x)\neq 0$, then the two execution orders differ in at least one protected observable distinction. They cannot be freely reordered. A declared 2-cell may still merge the square:

$$
d_1(\gamma)=\partial_{\text{comm}}^\rho(A,B;x),
$$

where $\gamma\in C^2$ is a conflict-resolution cell. If no such $\gamma$ is declared, the runtime has no certificate of confluence and must serialize or reject. $\square$

**Gate:** This is conditional on the faithfulness of $\rho$ and on implementing `CheckCommutation(OpId, OpId)` against the concrete $C^2$ conflict complex. Without that realization witness, SmplCache can claim only certified noncommutation for represented distinctions, not all database conflicts.

---

### Theorem 7 — Aggregate Repair Completeness (Proved and Executing in SmplCache Kernel)

**Statement:** For a cached aggregate query of the form `SELECT group_key, AGG(val) FROM R WHERE pred GROUP BY group_key`, define the repair class:

$$
C^0_{\mathrm{agg}} = \{\text{add contribution}, \text{remove contribution}, \text{move contribution}\}.
$$

The repair completeness depends on the aggregate type. The theorem states:

| Aggregate | Repair complete with ordinary before/after CDC? |
| --------- | ----------------------------------------------- |
| SUM       | Yes                                             |
| COUNT     | Yes                                             |
| AVG       | Yes, via SUM + COUNT                            |
| MIN/MAX   | No, unless auxiliary extremum structure exists  |

For SUM/COUNT/AVG, an event $e$ is aggregate-repairable iff the CDC event provides:
1. The old and new values of $\texttt{val}$;
2. The old and new values of $\texttt{group\_key}$;
3. The old and new truth values of $\texttt{pred}$.

For MIN/MAX, full CDC is not enough unless the repair class includes an extremum index/heap. If the current extremum is removed, standard CDC cannot provide the next extremum.

**Why it matters:** This is the "SmplCache actually works" theorem. The Python prototype (`smplcache.py`) natively implements and enforces this exact topological bound for SUM/COUNT/AVG repair, correctly rejecting min/max and missing evidence.

#### Proof

Let $G=\{g_1,\ldots,g_m\}$ be the finite set of possible group keys for the cached shape, and let $e_g$ be the standard basis vector for group $g$.

For an update event $e$ with old row $r^-$ and new row $r^+$, define:

$$
\epsilon^- = 1[\mathrm{pred}(r^-)],
\quad
\epsilon^+ = 1[\mathrm{pred}(r^+)],
$$

$$
g^-=\mathrm{group}(r^-),
\quad
g^+=\mathrm{group}(r^+),
\quad
v^-=\mathrm{val}(r^-),
\quad
v^+=\mathrm{val}(r^+).
$$

For `SUM`, the exact aggregate defect is:

$$
h_{\mathrm{SUM}}(e)
=
\epsilon^+v^+e_{g^+}
-
\epsilon^-v^-e_{g^-}
\in \mathbb{R}^G.
$$

Let $C^1_{\mathrm{SUM}}=\mathbb{R}^G$. Let $C^0_{\mathrm{SUM}}$ be generated by elementary repair programs:

$$
\mathrm{add}(g,a),\quad
\mathrm{remove}(g,a),\quad
\mathrm{move}(g,h,a).
$$

Define $d_0$ by:

$$
d_0(\mathrm{add}(g,a))=ae_g,
$$

$$
d_0(\mathrm{remove}(g,a))=-ae_g,
$$

$$
d_0(\mathrm{move}(g,h,a))=-ae_g+ae_h.
$$

If the CDC event supplies $v^-,v^+,g^-,g^+,\epsilon^-,\epsilon^+$, then the repair vector

$$
\alpha
=
\mathrm{add}(g^+,\epsilon^+v^+)
+
\mathrm{remove}(g^-,\epsilon^-v^-)
$$

satisfies $d_0\alpha=h_{\mathrm{SUM}}(e)$. Therefore the SUM cache can be repaired exactly.

For `COUNT`, take $v^-=v^+=1$. The defect becomes:

$$
h_{\mathrm{COUNT}}(e)
=
\epsilon^+e_{g^+}
-
\epsilon^-e_{g^-},
$$

which is the same proof with unit contributions.

For `AVG`, maintain the pair:

$$
(\mathrm{SUM},\mathrm{COUNT})\in \mathbb{R}^G\oplus\mathbb{R}^G.
$$

The defect is:

$$
h_{\mathrm{AVG}}(e)
=
\left(h_{\mathrm{SUM}}(e),h_{\mathrm{COUNT}}(e)\right).
$$

Since both coordinates are repairable by the previous two arguments, the maintained pair is repairable. The displayed average is derived as:

$$
\mathrm{AVG}(g)=\frac{\mathrm{SUM}(g)}{\mathrm{COUNT}(g)}
$$

whenever $\mathrm{COUNT}(g)\neq 0$. Thus AVG is repairable through SUM plus COUNT.

It remains to prove the evidence condition. If the old value, new value, old group, new group, old predicate truth value, or new predicate truth value is not available, then there exist two concrete events indistinguishable to the certifier but inducing different $h(e)$. For example, if $v^-$ is hidden, deleting a matching row of value $10$ and deleting a matching row of value $20$ have the same visible event but require different repairs. A deterministic local repair function cannot be correct for both. The same indistinguishability argument applies to missing group keys and predicate membership. Therefore this evidence is necessary for a complete deterministic certifier over the event class.

For `MIN` and `MAX`, ordinary before/after CDC is insufficient. Consider two old groups:

$$
\{1,2\}
\quad\text{and}\quad
\{1,100\}.
$$

In both cases the CDC event "delete the row with value $1$" has the same old contribution. But after the delete, the new minimum is $2$ in the first group and $100$ in the second. The next extremum is not determined by the deleted row's before/after image. Therefore no repair program using only ordinary row CDC can be complete for MIN/MAX. Completeness requires auxiliary group state such as an ordered index, heap, or multiset count of values. $\square$

**Product consequence:** The CLI must not label an aggregate event "repairable" merely because `aggregate_cols` is nonempty. It may label it repairable only when the aggregate class and CDC evidence satisfy this theorem.


---

### Theorem 8 — Topological Workload Decomposition (Proved and Executing via TopoMap CLI)

**Statement:** Given $n$ query shapes with dependency fingerprints, define the shape coupling graph $G_W$ where shapes are vertices and an edge $(s_i, s_j)$ exists iff there exists an event $e$ that invalidates both $s_i$ and $s_j$.

Then:
1. Connected components of $G_W$ are independently invalidatable cache partitions.
2. $\beta_1(G_W) = |E| - |V| + |C|$ counts independent cycles in the observed co-invalidation graph.
3. A workload is **topologically clean** iff $\beta_1(G_W) = 0$ (tree-like coupling).

**Why it matters:** The SmplCache CLI (`cli.py`) actively computes this via the `--topomap` flag, proving that the connected-component decomposition is mathematically sound for cache partitioning. This is the core engine behind the workload advisor.

#### Proof

Let $I(e)\subseteq V(G_W)$ be the set of shapes invalidated by event $e$. By construction, if $s_i,s_j\in I(e)$ with $i\neq j$, then $(s_i,s_j)$ is an edge of $G_W$.

Therefore, if an event invalidates more than one shape, the invalidated shape set $I(e)$ forms a clique in $G_W$. Every clique lies inside a single connected component. Hence no single event can invalidate shapes in two different connected components. This proves that connected components are independently invalidatable cache partitions. Isolated shapes are singleton partitions.

For a finite undirected graph with $V$ vertices, $E$ edges, and $C$ connected components, the rank of the first graph homology group is:

$$
\beta_1(G_W)=|E|-|V|+|C|.
$$

This is the standard cycle-rank formula. It follows by choosing a spanning forest: a forest on $|V|$ vertices and $|C|$ components has $|V|-|C|$ edges. Every additional edge closes exactly one independent cycle. Therefore the number of independent cycles is:

$$
|E|-(|V|-|C|)=|E|-|V|+|C|.
$$

Thus $\beta_1>0$ iff the co-invalidation graph contains at least one independent cycle. These cycles are not artifacts of the formula; they are exactly redundant paths in the observed co-invalidation relation.

A workload is topologically clean in this graph sense iff $\beta_1(G_W)=0$, i.e. iff every connected component is a tree. This means coupling is acyclic. It does not mean there is no invalidation churn; it means the coupling has no cyclic redundancy. $\square$

**Implementation note:** `calculate_topomap` currently counts active coupled nodes. For partition accounting, the implementation should also report isolated singleton partitions.

---

### Theorem 9 — Schema-Adaptive Repair Class Lifting (One-way proved; original iff corrected)

**Original proposed statement:** Given a workload $W$ with quotient obstruction space $Q^1_W$, and a proposed schema migration $M$, the post-migration repair class $C^0_{M}$ satisfies:

$$
\dim Q^1_{M} \leq \dim Q^1_W
$$

iff the migration adds repair capacity without removing existing repairs.

If $\dim Q^1_M > \dim Q^1_W$, the migration has *increased* cache obstruction. SmplCache should warn.

This statement is too strong as written. The corrected theorem below proves the useful one-way implication and gives the missing witness condition.

**Why it matters:** This turns SmplCache into a schema migration advisor. No incumbent does this. Oracle's schema evolution tools tell you about constraint violations, not about cache repairability impact.

#### Corrected theorem

Place pre-migration and post-migration event distinctions in a common finite vector space $C^1$. Let:

$$
R_W=\operatorname{im}(d_{0,W}),
\quad
R_M=\operatorname{im}(d_{0,M}).
$$

If the migration preserves all existing repairs and adds only repair capacity, meaning:

$$
R_W\subseteq R_M,
$$

then:

$$
\dim Q^1_M \leq \dim Q^1_W.
$$

If $\dim Q^1_M>\dim Q^1_W$, then the migration has strictly increased the obstruction rank and SmplCache should warn.

The converse is not true from dimensions alone. To certify a safe lift, SmplCache needs an image-inclusion witness $R_W\subseteq R_M$, not merely a smaller quotient dimension.

#### Proof

Because $Q^1_W=C^1/R_W$ and $Q^1_M=C^1/R_M$, rank-nullity gives:

$$
\dim Q^1_W=\dim C^1-\dim R_W,
$$

$$
\dim Q^1_M=\dim C^1-\dim R_M.
$$

If $R_W\subseteq R_M$, then $\dim R_W\leq \dim R_M$. Therefore:

$$
\dim C^1-\dim R_M
\leq
\dim C^1-\dim R_W,
$$

so $\dim Q^1_M\leq \dim Q^1_W$.

If $\dim Q^1_M>\dim Q^1_W$, then $\dim R_M<\dim R_W$. The post-migration repair image has lower rank in the common distinction space, so some independent repair capacity has been lost. This is a valid warning condition.

However, $\dim Q^1_M\leq\dim Q^1_W$ does not imply $R_W\subseteq R_M$. In $\mathbb{R}^2$, the $x$-axis and $y$-axis have the same dimension, but neither contains the other. A migration could replace one repair direction with another and keep the same quotient dimension while losing an old repair. Therefore the original dimension-only "iff" is false without an inclusion witness. $\square$

---

### Theorem 10 — Observer-Relative Cache Consistency (Proved for compatible quotient masks)

**Statement:** Let $\mathcal{O}_A$ and $\mathcal{O}_B$ be two observer quotient maps (tenant views, role-based access masks, RLS policies). By the Observer Passivity theorem (O1 from general_theory.md):

$$
\Phi_{\mathcal{O}}(\mathcal{O}(q)) \leq \Phi(q).
$$

Therefore a repair that is valid at the core level is valid for every observer. But an observer may see $\Phi_{\mathcal{O}}(\mathcal{O}(q)) = 0$ when $\Phi(q) > 0$ — the observer's coarser view makes the obstruction invisible.

**Why it matters:** Multi-tenant cache. If tenant A's view doesn't touch the obstructed columns, tenant A's cache can be repaired even when the global cache cannot. This is a direct product advantage over Redis (no per-tenant intelligence) and over PostgreSQL RLS (which invalidates everything on row mutation).

#### Proof

Let the core repair complex be:

$$
C^0 \xrightarrow{d_0} C^1,
\quad
Q^1=C^1/\operatorname{im}(d_0).
$$

Let an observer mask be a quotient map:

$$
\mathcal{O}_1:C^1\to C^1_{\mathcal{O}}.
$$

Assume the observer has a compatible repair complex:

$$
C^0_{\mathcal{O}} \xrightarrow{d_{0,\mathcal{O}}} C^1_{\mathcal{O}},
$$

and a repair projection $\mathcal{O}_0:C^0\to C^0_{\mathcal{O}}$ such that the square commutes:

$$
\mathcal{O}_1 d_0 = d_{0,\mathcal{O}}\mathcal{O}_0.
$$

If a core event is repairable, then $h=d_0\alpha$ for some $\alpha\in C^0$. Applying the observer quotient:

$$
\mathcal{O}_1(h)
=
\mathcal{O}_1d_0\alpha
=
d_{0,\mathcal{O}}\mathcal{O}_0\alpha.
$$

Thus $\mathcal{O}_1(h)\in\operatorname{im}(d_{0,\mathcal{O}})$, so the observer-level obstruction class is zero:

$$
[\mathcal{O}_1(h)]=0\in Q^1_{\mathcal{O}}.
$$

Therefore every core-valid repair remains valid after a compatible observer quotient.

A nonzero core obstruction may become zero after masking. This happens when $h\notin\operatorname{im}(d_0)$ but $\mathcal{O}_1(h)\in\operatorname{im}(d_{0,\mathcal{O}})$, including the common case $\mathcal{O}_1(h)=0$. Thus an observer can see less obstruction than the core, but not more, provided the quotient is compatible. $\square$

**Implementation gate:** `ApplyMask(PolicyId)` must be shown to implement such a compatible quotient. If a policy transform computes new derived fields or leaks hidden coordinates into visible ones, compatibility must be proved for that policy separately.

---

### Theorem 11 — No-Rabbit Realization for SQL (Proved and Executing via Strict Fragment Extractor)

**Statement:** By Theorem I3 (No-Rabbit) from general_theory.md, SmplCache cannot claim that the quotient obstruction space $Q^1_{\mathrm{cache}}$ captures all possible cache coherence failures unless the realization functor from SQL semantics to the cochain complex is explicitly constructed and shown to be faithful.

The realization theorem must produce two sets:

$$
D_{\text{captured}} \quad \text{and} \quad D_{\text{missed}}
$$

Then:

$$
D_{\text{missed}}=\varnothing \implies \text{soundness}.
$$

*   **Safe for over-invalidation:** If every semantic dependency is included in the fingerprint, it may over-invalidate (e.g. `WHERE amount > 100`, changing amount from 50 to 60 invalidates anyway). That is safe but wasteful.
*   **Unsafe:** If expression-level dependencies are missed by the parser, it can under-invalidate.

Completeness requires more:

$$
\text{captured dependencies are not merely columns, but semantic predicate-transition dependencies.}
$$

**Why it matters:** This is the honesty theorem. Without it, SmplCache overclaims. With it, SmplCache can precisely bound its detection rate and declare what it does not cover.

**Realization obligation:** Define the realization functor $R: \mathsf{SQL}_{\mathrm{shapes}} \to \mathsf{Cochain}$ explicitly. For each SQL feature, build the explicit sets $D_{\text{captured}}$ and $D_{\text{missed}}$.

$$
\boxed{
\text{No realization functor, no completeness claim. Soundness only.}
}
$$

#### Restricted realization (Executing in `extractor.py`)

Let $\mathsf{SQL}_{\mathrm{frag}}$ be the finite SQL fragment consisting of single-block:

- `SELECT`
- `FROM`
- deterministic scalar expressions
- inner equijoins
- `WHERE`
- `GROUP BY`
- `SUM`, `COUNT`, and `AVG`

Exclude subqueries, window functions, nondeterministic functions, outer joins, `LIMIT/OFFSET`, triggers with hidden side effects, and user-defined functions unless they declare dependencies.

Define:

$$
R:\mathsf{SQL}_{\mathrm{frag}}\to\mathsf{Cochain}
$$

by mapping each query shape to a finite dependency fingerprint with role labels:

$$
(\mathrm{relation},\mathrm{column},\mathrm{role})
$$

where role is one of:

$$
\mathrm{predicate},\mathrm{projection},\mathrm{aggregate},\mathrm{group},\mathrm{join},\mathrm{security}.
$$

Every column referenced by a predicate expression is placed in predicate dependencies. Every selected expression contributes projection dependencies. Every aggregate argument contributes aggregate dependencies. Every group expression contributes group dependencies. Every equijoin key contributes join dependencies. Every policy predicate or mask input contributes security dependencies.

#### Restricted soundness proof

Let $S$ be a query in $\mathsf{SQL}_{\mathrm{frag}}$. Suppose two database states agree on all coordinates in $R(S)$ for all rows whose membership can be observed by $S$. Then:

1. All deterministic scalar expressions in `SELECT` evaluate equally, because their input columns agree.
2. All `WHERE` predicate truth values agree, because predicate input columns agree.
3. All equijoin memberships agree, because join-key columns agree.
4. All group keys agree, because group-expression input columns agree.
5. All SUM/COUNT/AVG aggregate contributions agree, because predicate membership, group keys, and aggregate inputs agree.
6. All security masks agree, because policy inputs agree.

Therefore the query result of $S$ is equal in the two states.

Now consider a CDC event whose changed boundary is disjoint from $R(S)$. The old and new states agree on every dependency coordinate of $S$, so by the previous paragraph the result of $S$ is unchanged. Preserving the cache is sound.

If the event intersects $R(S)$, SmplCache may invalidate or require a repair certificate. This may over-invalidate, but it does not under-invalidate inside the fragment. $\square$

#### Completeness boundary

For $\mathsf{SQL}_{\mathrm{frag}}$, $D_{\mathrm{missed}}=\varnothing$ only if the extractor captures every column read by every deterministic expression and policy. For general SQL, $D_{\mathrm{missed}}$ is not empty until the realization covers the excluded constructs. Therefore full SQL completeness remains open.

**Safe claim:** SmplCache is sound for the declared SQL fragment under complete dependency extraction. Outside that fragment, it must either conservatively invalidate or mark the shape unsupported.

---


### Theorem 12 — Lock Demotion Theorem (Conditional proof for logical lock demotion)

**Statement:** Let $U_A, U_B$ be two write operators. If their commutator boundary vanishes:

$$
\partial_{\mathrm{comm}}(A,B)=0,
$$

and both repair certificates are independent, then $U_A$ and $U_B$ may be scheduled without mutual exclusion.

If the commutator is nonzero or uncertified, the scheduler must serialize, merge via a declared 2-cell, or refuse.

**Why it matters:** This gives the clean systems line:

$$
\boxed{
\text{Locks are not primitives. Locks are failed commutation certificates.}
}
$$

#### Proof

Assume:

1. $\partial_{\mathrm{comm}}^\rho(A,B;x)=0$ for the protected realization $\rho$.
2. The repair certificates for $A$ and $B$ are independent: their cache repair effects are either on disjoint coordinates or commute additively in the same coordinate space.
3. The storage runtime still provides atomic application of each individual write.

By Theorem 6, condition 1 implies:

$$
\rho(U_AU_Bx)=\rho(U_BU_Ax).
$$

Thus the two serial orders produce the same protected observer-visible state.

Let $r_A$ and $r_B$ be the certified cache repair operators. By condition 2:

$$
r_Ar_B = r_Br_A
$$

on the maintained cache coordinates. Therefore the final cache state is independent of whether the runtime applies the certified repairs in order $A$ then $B$ or $B$ then $A$.

The state projection and cache projection are both order-independent. Hence a logical mutual-exclusion lock between $A$ and $B$ is unnecessary for preserving protected observer-visible invariants. The scheduler may demote that logical lock.

If the commutator boundary is nonzero, observer-visible order dependence exists. If it is uncertified, the runtime has no proof that order dependence is absent. In either case, demotion is not justified; the scheduler must serialize, merge via a declared 2-cell, or refuse. $\square$

**Systems boundary:** This theorem demotes logical conflict locks. It does not eliminate implementation-level synchronization needed for memory safety, atomic writes, or durable log ordering.


## Part IV — Product Stack Architecture

### The Stack Roadmap

| Layer | Role | Quotient structure | Status |
|---|---|---|---|
| **SmplCache** | Cache repair/invalidation certificates | $Q^1_{\mathrm{cache}}$ from query shape × CDC | Python prototype operational |
| **SmplCDC** | Typed write-event boundary extraction | $h(e) \in C^1$ from raw WAL/log | Designed, not built |
| **SmplPlan** | Query-plan sensitivity analysis | $C^1_{\mathrm{plan}}$ from plan operator tree | Not started |
| **SmplWAL** | WAL entries carrying repair/obstruction metadata | $(h, \Phi, \Gamma, \text{cert})$ per entry | Not started |
| **SmplStore** | Storage layout selected by quotient obstruction | Layout minimizing $\dim Q^1$ | Not started |
| **SimplexVM** | Boundary-native execution engine | Full $C^0 \to C^1 \to C^2$ pipeline | Rust prototype operational |
| **AutoPDB** | Categorical object store / schema graph | CategoryGraph with 4-index model | Rust implementation operational |
| **ATPU** | AutoP language compiler for boundary programs | Lexer → Parser → Codegen → VM | Rust implementation operational |

SmplCache is layer 1. The theory is the same at every layer.

### Existing Implementation Assets

| Component | Location | What it does |
|---|---|---|
| `smplcache.py` | `.codename_simplexdb/smplcache/` | Python fingerprint simulator: QueryShape, WriteEvent, process_event |
| `cli.py` | `.codename_simplexdb/smplcache/` | Workload advisor: false invalidation analysis, topomap, Betti-1, entropy, gravity wells |
| `simplex_vm` | `.codename_simplexdb/simplex_vm/` | Rust VM: AttachState, AttachEdge, ComputeBoundary, RouteBoundary, ApplyMask, UpdateAggregate, EmitCdc |
| `autopdb.rs` | `.codename_simplexdb/.autopdb/` | Categorical object store with PyO3 bridge, JSONL/YAML/SQL boot, gap detection, composition |
| `ATPU/autop` | `.codename_simplexdb/.atpu/autop/` | Full compiler pipeline: lexer, parser, codegen, string diagrams, boundary check, eager validator |
| AutoP grammar | `omega_core/law/grammar/autop/grammar/` | EBNF + ANTLR4 grammar for the AutoP proof language |

---

## Part V — The TPU Endpoint

A Topological Processing Unit, in this sense, is not a GPU clone. It is a hardware/software boundary engine that accelerates:

- $d_0 \alpha = h$ membership checks (is this write repairable?)
- $[h] \in Q^1$ quotient projections (what is obstructed?)
- $p(x) = p(y), \; p(\rho(c)x) \neq p(\rho(c)y)$ witness detection (is the profile too coarse?)
- local repair synthesis (compute $\alpha$ from $h$)

The CPU stops being the universal janitor. It boots the OS, handles control, and delegates invariant-preserving state movement to a boundary processor.

This is a legitimate architecture thesis, not a product claim. It requires SmplCache to work first.

---

## Part VI — Relationship to Classical Normal Forms

$$
\boxed{
\text{Classical normal forms classify static dependency anomalies. QNF classifies dynamic repair obstructions.}
}
$$

Codd/Boyce/Fagin/Date normal forms answer:

$$
\text{How should facts be decomposed to avoid update anomalies?}
$$

SmplCache answers:

$$
\text{Given this workload and this update stream, which cached facts can be repaired without recomputation?}
$$

Those are related, but not the same problem.

---

## Part VII — Naming

Do not lead with "QNF" — it risks sounding like another normal form.

Lead with:

$$
\boxed{\text{Quotient Repairability}}
$$

or:

$$
\boxed{\text{Boundary-Certified Cache Repair}}
$$

Then QNF can be the theoretical appendix:

> QNF is the normal-form view. SmplCache's operational theorem is quotient repairability.

---

## Part VIII — Claim Discipline

1. **What this supports:** The cochain construction provides a finite, computable framework for deciding cache repairability. It encodes the decision as a quotient membership test, and each decision carries a certificate. Under a dependency-specific encoding, known normal-form violations correspond to nonzero labeled obstruction classes. The existing implementations (Python SmplCache, Rust SimplexVM, AutoPDB, ATPU compiler) demonstrate structural feasibility.

2. **What this does not support:** This does not show that QNF is computationally faster than classical normalization algorithms in all cases. It does not show the cochain complex is the unique unification. It does not replace ACID compliance in the system of record. Full SQL completeness remains open beyond the restricted realization fragment in Theorem 11. Noncommutation detection in Theorem 6 is proved only conditionally on a faithful realization and still needs an implementation witness.

3. **Narrowest defensible claim:** Cache repairability is a property of the workload-schema-CDC triple, not of the schema alone. SmplCache's quotient test decides column-level preservation soundly for the declared SQL fragment. SUM, COUNT, and AVG aggregate repair are complete when the required before/after CDC evidence is present. MIN/MAX require auxiliary extremum state. Outside the declared fragment, SmplCache must conservatively invalidate or mark the shape unsupported.

4. **Next tests needed:**
   - Convert Theorem 7 into executable certifier tests for SUM, COUNT, AVG, and MIN/MAX obstruction.
   - Update the CLI so "repairable" is emitted only when the Theorem 7 evidence certificate exists.
   - Implement `CheckCommutation` in the SimplexVM beyond stub and bind it to the Theorem 6 conflict complex.
   - Build the restricted realization extractor from Theorem 11 for SELECT/WHERE/GROUP BY/JOIN.
   - Add TopoMap tests for isolated singleton partitions from Theorem 8.

---

## Part IX — Proof Target Register

| ID | Theorem | Status | Dependency | Priority |
|---|---|---|---|---|
| T1 | Workload Repairability | **Proved** | — | — |
| T2 | Normalization ≠ Repairability | **Proved** | — | — |
| T3 | Normal-Form Projection | **One-way proved** | Explicit $d_0$ matrices per dependency type | High |
| T4 | Context Witness = Lift Trigger | **Proved** | — | — |
| T5 | Boundary-Certified Write Path | **Proved** | — | — |
| T6 | Noncommutation Detection | **Conditional proof** | Faithful realization + $C^2$ implementation | Critical |
| T7 | Aggregate Repair Completeness | **Proved for SUM/COUNT/AVG; MIN/MAX obstruction proved** | Executable certifier tests | Critical |
| T8 | Topological Workload Decomposition | **Proved for finite graph** | Isolated partition reporting | High |
| T9 | Schema-Adaptive Lifting | **One-way proved; original iff corrected** | Image-inclusion witness | Medium |
| T10 | Observer-Relative Cache | **Proved for compatible quotient masks** | `ApplyMask` compatibility witness | High |
| T11 | No-Rabbit SQL Realization | **Restricted proof; full SQL open** | Realization extractor for declared fragment | Critical |
| T12 | Lock Demotion Theorem | **Conditional proof** | Certified commutation + atomic runtime | High |

---

## The Lines

$$
\boxed{
\text{Codd gave us data independence. Smpl gives cache repair independence.}
}
$$

$$
\boxed{
\text{The database industry treats writes as mutations. Smpl treats writes as boundaries.}
}
$$

$$
\boxed{
\text{A database should not guess what a write breaks. It should compute the obstruction.}
}
$$
