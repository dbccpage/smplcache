# Carrier Contract

## Classification
**Subsystem:** Cohomological Semantics
**Role:** Finite declared structure.

## Contract
A `Carrier` is a finite declared structure supporting cochains, repair maps, context actions, and obstruction measurement.

**Requires:**
* $C^0$, $C^1$, $C^2$
* $d_0: C^0 \to C^1$
* $d_1: C^1 \to C^2$
* $d_1 \circ d_0 = 0$
* `admissible_repair_set`
* `context_family`
* `coefficient_semantics`

**Must Not:**
* Be defined as "world", "model", "ontology", "semantic domain", or "embedding space".
