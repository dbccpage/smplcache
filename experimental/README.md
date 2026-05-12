# Experimental Workload Geometry

This directory contains experimental Rust code for generating an **Invalidation Correlation Matrix** from a cache workload.

smplcache starts with exact dependency fingerprints. The experimental matrix layer asks a larger question: what is the global geometry of cache invalidation across the whole workload?

By treating each cached query shape as a dimension, we map out co-invalidation patterns and compute:
- **Decoupling Score** (Trace of the squared matrix)
- **Invalidation Entropy** (How dispersed invalidation pressure is)
- **Principal Invalidation Components** (Eigendecomposition to identify dominant invalidation clusters)
- **Cache Gravity Wells**

*Note: density_matrix_28_demo.rs hardcodes a 28-dimensional matrix for demonstration purposes. Future implementations will use dynamic matrices.*
