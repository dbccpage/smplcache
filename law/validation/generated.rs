use super::file_audit::{FileAuditSubject, FilePart};

pub static FILE_AUDIT_MANIFEST: &[FileAuditSubject] = &[
    FileAuditSubject {
        path: "src/types/foo.rs",
        declared_kind: "BaseType",
        schema_hash: 0x1234,
        parts: &[
            FilePart { name: "BaseType impl", required: true, kind: "trait_impl" },
            FilePart { name: "contract binding", required: true, kind: "contract" },
            FilePart { name: "validation unit", required: true, kind: "validator" },
        ],
    },
];
