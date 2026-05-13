# DecoderOp Kernel

## Classification
**Subsystem:** Representation operator
**Signature:** `EncodedRepresentation → StructuredRepresentation`
**Role:** Inverse or partial inverse of Encoder.

## Contract
A `DecoderOp` takes a dense, constrained, or physical representation and re-inflates it into a structured categorical topology.

**Requires:**
* Decoding certificate.
* Loss/ambiguity declaration.
* Source encoding reference.

**Must Not:**
* Invent missing structure.
* Claim physical interpretation.
* Repair malformed encoding.
