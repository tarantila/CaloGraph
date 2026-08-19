# Apple Health setup

## Automatic transfer

1. Install Health Auto Export on the iPhone and grant HealthKit access to the
   required nutrition categories. If activity credit is to be used, additionally
   grant **Active Energy Burned**.
2. Create a device-specific import token in CaloGraph.
3. Create a REST API automation using JSON, Export Version 2, and the import
   endpoint.
4. Set `Authorization` to `Bearer <Token>`.
5. Test the validation endpoint first, then switch to the normal import
   endpoint.
6. Send an overlapping seven-day range to capture delayed changes.

iOS may delay exports while the device is locked, Low Power Mode is active, or
Background App Refresh is disabled. CaloGraph cannot bypass these platform
restrictions.

## Historical data

Open Apple Health, select the profile picture, and choose **Export All Health
Data**. Upload the ZIP unchanged through the CaloGraph import view. Never send
an export file to third parties.

Apple Health includes measurements and sources, but does not reliably include
food, recipe, or meal names.
