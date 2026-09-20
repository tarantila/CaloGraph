GOOGLE_HEALTH_API_BASE_URL = "https://health.googleapis.com/v4"
GOOGLE_HEALTH_ACTIVE_ENERGY_BURNED_PATH = (
    "/users/me/dataTypes/active-energy-burned/dataPoints"
)
GOOGLE_HEALTH_WEIGHT_PATH = "/users/me/dataTypes/weight/dataPoints"
GOOGLE_HEALTH_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_HEALTH_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_HEALTH_NUTRITION_SCOPE = "https://www.googleapis.com/auth/googlehealth.nutrition.readonly"
GOOGLE_HEALTH_ACTIVITY_SCOPE = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly"
)
GOOGLE_HEALTH_HEALTH_METRICS_SCOPE = (
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly"
)
GOOGLE_HEALTH_SCOPES = (
    GOOGLE_HEALTH_NUTRITION_SCOPE,
    GOOGLE_HEALTH_ACTIVITY_SCOPE,
    GOOGLE_HEALTH_HEALTH_METRICS_SCOPE,
)
GOOGLE_HEALTH_REQUIRED_SCOPES = frozenset(GOOGLE_HEALTH_SCOPES)
GOOGLE_HEALTH_CALLBACK_PATH = "/api/v1/google-health/oauth/callback"
