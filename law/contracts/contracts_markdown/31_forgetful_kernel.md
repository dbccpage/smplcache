# ForgetfulOp Kernel

## Classification
**Subsystem:** Projection/forgetting operator
**Role:** Lawful information forgetting (`RichStructure → PoorerStructure`).

## Contract
A `ForgetfulOp` represents a forgetful functor from a rich categorical structure to a poorer one (e.g., dropping the metric to keep only the topology).

**Must:**
* Forget structure lawfully.
* Issue a loss certificate.

**Must Not:**
* Destroy structure without producing a cryptographic/traceable certificate of what was forgotten.
