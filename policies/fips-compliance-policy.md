# Example Drives FIPS compliance policy (fictional)

This is a fictional policy written for a portfolio project.

## Section F1: Which drives are FIPS certified

Only drive models whose SKU ends in "-F" are FIPS 140-3 certified. All other SKUs are not FIPS certified, even if they support encryption.

## Section F2: Firmware requirement

A FIPS-certified drive keeps its validated status only when running firmware version 3.2 or later. Drives on older firmware must be updated before being used where FIPS 140-3 is required.

## Section F3: Government and regulated use

Non-FIPS drives must not be recommended for government or regulated workloads that require FIPS 140-3. Reps should suggest an equivalent "-F" SKU instead.
