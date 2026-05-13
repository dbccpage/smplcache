```yaml
contract:
  name: CertificateKernel
  role: verification infrastructure
  depends_on:
    - AnalysisUnit
  output_family:
    - Certificate
    - ProofWitness
```
# Certificate Kernel Contract

## Core Definition

> The CertificateKernel provides a unified, typed infrastructure for all verifiable proof artifacts in the Omega Engine. It binds a claim to its evidence. A Certificate is not authority. It is checkable structure.

---

## Unified Certificate Types

### 1. Certificate Identity
```rust
pub struct CertificateId(pub Blake3Hash);

pub struct CertificateScope {
    pub subject: BoundaryRef,
    pub invariant_id: InvariantId,
    pub timestamp: Epoch,
}
```

```rust
pub struct CertificateId(pub Blake3Hash);

pub struct CertificateScope {
    pub subject: BoundaryRef,
    pub invariant_id: InvariantId,
    pub timestamp: Epoch,
}
```

### 2. Typed Certificates
```rust
pub enum Certificate {
    SolverCertificate(SolverCertificateData),
    AnalysisCertificate(AnalysisCertificateData),
    ReplayCertificate(ReplayCertificateData),
    WitnessCertificate(WitnessCertificateData),
    LiftLicenseCertificate(LiftLicenseData),
    TraceCertificate(TraceCertificateData),
}
```

### 3. Verification Artifact
```rust
pub enum VerificationArtifact {
    /// Exact symbolic witness (e.g., integer residual)
    Symbolic(SymbolicWitness),
    /// Numerical witness with explicit tolerance
    Numerical {
        value: MeasurementValue,
        tolerance: ToleranceSpec,
        method: SolverId,
    },
    /// Cryptographic or structural hash of a checked property
    Structural(HashWitness),
}
```

### 3. Tolerance Specification
```rust
pub enum ToleranceSpec {
    /// Exact match required
    Exact,
    /// Absolute error bound
    Absolute(f64),
    /// Relative error bound
    Relative(f64),
    /// Bound on a specific norm (e.g., L1, L2, Inf)
    Norm { value: f64, norm: NormKind },
}
```

### 4. Proof Witness
```rust
pub struct ProofWitness {
    pub certificate_id: CertificateId,
    pub artifact: VerificationArtifact,
    pub generator: AnalysisUnitId,
    pub dependencies: Vec<CertificateId>,
}
```

---

## Contractual Invariants

### 1. Independent Checkability
> **A Certificate must be independently checkable within its declared scope.**

A certificate is invalid if it does not contain sufficient data to independently re-verify the claim. "Trust but verify" is replaced by "Don't trust, re-run the analysis unit."

### 2. Evidence Addressability
Every certificate must point to the exact artifact and state from which it was derived.

### 3. Immutability
Once a certificate is issued for a state, it is immutable. Any change to the state invalidates the certificate scope.

---

## Subsystem Integration

| Subsystem | Role in Certificate Kernel |
|---|---|
| **Analysis** | Generates certificates as factual outputs. |
| **Diagnostic** | References certificates as evidence for findings. |
| **Evaluator** | Consumes certificates to gate admissibility. |
| **Meta** | Validates certificate chains for multi-stage lifts. |
| **Engine** | Persists certificates in the trace for auditability. |

---

## Canonical Invariant

> **A claim without a certificate is a hypothesis; a certificate without a witness is a lie.**
