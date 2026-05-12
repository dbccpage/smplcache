# smplcache

**Don’t invalidate what you can repair.**

smplcache is not a cache server. It is a workload-shape advisor for CDC-backed caches.

It compares blind table-level invalidation against query-shape dependency invalidation, detects repairable aggregates, maps cache coupling, and flags common query-shape pathologies like implicit conversions, missing indexes, and parameter skew.

## CLI Tools

`smplcache` exposes four workload-shape tools.

### `report`

Analyze cache invalidation behavior from a workload JSON file.

```bash
python cli.py report examples/workload.common.json --format markdown --topomap
```

Reports:

* false invalidations avoided
* shape-level invalidations
* avoided invalidations by event
* repairable aggregate updates
* top invalidating columns
* coupled query shapes
* TopoMap geometry and recommendations

### `doctor`

Detect query-shape pathologies.

```bash
python cli.py doctor examples/workload.common.json --format markdown
```

Finds:

* implicit conversion risks
* missing index candidates
* parameter skew
* observed shape-type splits

### `compare`

Compare an obstructed workload against a lifted workload.

```bash
python cli.py compare examples/obstruction_mess.json examples/lifted_clean.json --format markdown
```

Shows:

* coupling reduction
* Betti-1 cycle reduction
* obstruction score before/after
* suggested structural lift

### `matrix` experimental

Analyze the workload as an invalidation correlation matrix.

```bash
python cli.py matrix examples/obstruction_mess.json examples/lifted_clean.json
```

Shows:

* dominance score
* invalidation entropy
* dominant invalidation component
* number of shapes controlled by the dominant mode

This command is experimental. The core `smplcache` logic does not depend on matrix geometry.

