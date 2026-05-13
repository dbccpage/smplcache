# ReflectorOp Kernel

## Classification
**Subsystem:** Licensed algebraic operator
**Signature:** `S → Quote(S) | Reflection(S)`
**Role:** Creates self-reference / quotation / internal representation.

## Contract
A `ReflectorOp` reifies the structure of a state into a higher-order structure that can be reasoned about internally.

**Requires:**
* Reflection license.
* Quote/decode boundary.
* No execution by reflection.

**Must Not:**
* Execute quoted objects.
* Grant authority.
* Create hidden recursion.
