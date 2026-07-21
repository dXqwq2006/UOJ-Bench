# Probability And Randomization

## Topic-Specific Variants

- Birthday amplification: many comparable objects so one collision/bad sample becomes likely.
- Correlated trials: repeated randomized attempts draw from the same effective state class.
- Adversarial order for expected-time algorithms, with the same values as a benign order.
- Random pruning where the valid branch has tiny measure but can be constructed directly.

## Strong Adversarial Solutions

- Random trials are independent after preprocessing or seed mixing.
- Expected-time behavior survives adversarially ordered but legal input.
- A rare event is impossible because each individual comparison has low probability.
- Random pruning is safe because every branch has many equivalent alternatives.

## Killer Constructions

- Use structured inputs for seed sweeps instead of uniform random inputs.
- Batch many equality/collision/pivot opportunities in one case.
- Pair benign order and adversarial order with the same underlying objects.
- Construct the unique valid branch, then surround it with many random-looking dead branches.
