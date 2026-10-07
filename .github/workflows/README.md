# Archived upstream workflows

The inherited Meetily workflow definitions have a `.disabled` suffix. GitHub
Actions only discovers `.yml` and `.yaml` workflow files, so these definitions do
not run when oats source is pushed. They are kept as historical build references.

This public repository currently publishes source and documentation, not
automated installers or releases. The other guides in this directory describe
the upstream workflow design; their triggers and release instructions are not
active for oats. Re-enabling any workflow requires adapting its repository,
application identity, model/runtime setup, signing, permissions, and release
destinations, followed by review.
