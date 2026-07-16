from django.contrib.auth.tokens import PasswordResetTokenGenerator


class SignupPasswordResetTokenGenerator(PasswordResetTokenGenerator):
    """
    Django's default_token_generator assumes a django.contrib.auth.models.User
    instance (it reads user.last_login and user.get_email_field_name()).
    Our Signup model has neither, so we override the hash value to use just
    the fields Signup actually has: pk, password, and email.

    This keeps the same security properties that matter:
    - The token embeds the current password hash, so it's automatically
      invalidated the instant the password changes (one-time use).
    - It's still HMAC-signed with the project's SECRET_KEY, so it can't be
      forged without that key.
    - It still expires after settings.PASSWORD_RESET_TIMEOUT seconds
      (3 days by default), since the base class enforces that in check_token().

    The only thing we lose vs. the original is invalidation-on-login (since
    Signup doesn't track last_login) - acceptable here because the token
    already self-invalidates on password change and on the time limit.
    """

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.password}{timestamp}{user.email}"


signup_token_generator = SignupPasswordResetTokenGenerator()
