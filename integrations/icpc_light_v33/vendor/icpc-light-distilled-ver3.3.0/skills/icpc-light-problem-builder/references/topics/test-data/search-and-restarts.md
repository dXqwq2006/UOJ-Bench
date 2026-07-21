# Search And Restarts

## Topic-Specific Variants

- Many near-solutions around a bad basin, with the true basin separated by a low-probability move.
- Thin feasible region that random perturbation almost never enters from the natural initialization.
- Restart clones: distinct seeds still produce the same initial state class or path.
- Repair cycle where local fixes alternate between a small set of violations.

## Strong Adversarial Solutions

- Independent restarts explore meaningfully different basins.
- First feasible is good enough because quality gaps are smooth.
- Rare moves can be pruned without losing the only route to feasibility.
- Random tie-breaking is diverse when the state representation collapses many seeds.

## Killer Constructions

- Create equivalent-looking basins where only one has a path to optimum.
- Force repairs to alternate between two violations unless a global move is made.
- Design seed sweeps around the same adversarial landscape, not around unrelated random cases.
- Add a scoring cliff after feasibility so weak local search accepts a poor basin.
