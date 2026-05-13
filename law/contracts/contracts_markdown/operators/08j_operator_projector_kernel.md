# ProjectorOp Kernel

## Classification
**Subsystem:** Projection/forgetting operator
**Signature:** `S → Substructure(S) | Quotient(S)`
**Role:** Projects onto declared subspace, quotient, basis, or support.

## Contract
A `ProjectorOp` explicitly maps a rich structure into a quotient space or a substructure.

**Requires:**
* Projection target.
* Kernel/loss declaration.
* Quotient impact.

**Must Not:**
* Hide information loss.
* Pretend projection is equivalence.
* Use projection as acceptance.
