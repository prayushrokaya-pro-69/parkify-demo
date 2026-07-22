"""
Google sign-in bridge for Parkify.

Parkify does NOT use django.contrib.auth's User model for logged-in
accounts - it has its own `Signup` model and does its own session-based
login (see `authentication()` in views.py, which sets
request.session['user_id'] / ['username'] / ['role']).

django-allauth, out of the box, wants to create/attach a
django.contrib.auth User whenever someone signs in with Google. That
would produce an account the rest of this project knows nothing about.

This adapter hooks into allauth right after Google has confirmed the
person's identity (pre_social_login) and, instead of letting allauth
continue its normal flow, it:

    1. Reads the verified email address Google gave us.
    2. Finds the matching `Signup` row, or creates a new one.
    3. Logs that person in exactly the way the normal username/password
       form does - by writing to request.session - re-using the same
       2FA / reactivation checks as the regular login path.
    4. Redirects, short-circuiting allauth so it never touches
       django.contrib.auth or creates a SocialAccount/User row.
"""

from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.core.files.base import ContentFile
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.crypto import get_random_string
from django.utils.http import url_has_allowed_host_and_scheme

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from .models import Signup
from .views import _send_login_otp, _send_reactivation_otp, _role_dashboard_name


class ParkifySocialAccountAdapter(DefaultSocialAccountAdapter):

    def pre_social_login(self, request, sociallogin):
        # Only Google is configured for this project right now, but this
        # guard keeps things safe if another provider is ever added.
        if sociallogin.account.provider != "google":
            return

        extra_data = sociallogin.account.extra_data or {}
        email = (extra_data.get("email") or sociallogin.user.email or "").strip().lower()

        if not email:
            messages.error(
                request,
                "Google didn't share an email address with us, so we couldn't sign you in."
            )
            raise ImmediateHttpResponse(redirect("authentication"))

        if not extra_data.get("email_verified", True):
            messages.error(
                request,
                "Please verify your email address with Google before continuing."
            )
            raise ImmediateHttpResponse(redirect("authentication"))

        next_url = self._safe_next(request, sociallogin)

        user = Signup.objects.filter(email__iexact=email).first()
        is_new_account = user is None

        if is_new_account:
            user = self._create_signup_from_google(email, extra_data)

        # ---- Same account-state checks as the normal login view ----

        if not user.is_active:
            _send_reactivation_otp(user)
            request.session["pending_reactivation_user_id"] = user.id
            if next_url:
                request.session["login_next"] = next_url
            messages.info(
                request,
                "This account is deactivated. We've emailed you a code to reactivate it."
            )
            raise ImmediateHttpResponse(redirect("reactivate_account"))

        if user.two_factor_enabled:
            _send_login_otp(user)
            request.session["pending_2fa_user_id"] = user.id
            if next_url:
                request.session["login_next"] = next_url
            raise ImmediateHttpResponse(redirect("verify_otp"))

        # ---- Log them in the same way authentication() does ----

        request.session["user_id"] = user.id
        request.session["username"] = user.username
        request.session["role"] = user.role

        if is_new_account:
            messages.success(request, f"Welcome to Parkify, {user.first_name or user.username}!")
        else:
            messages.success(request, f"Welcome back, {user.username}!")

        raise ImmediateHttpResponse(
            redirect(next_url or reverse(_role_dashboard_name(user.role)))
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_next(self, request, sociallogin):
        """Mirrors views._get_safe_next(), but works off allauth's state dict
        (the ?next= that was on the /accounts/google/login/ link)."""
        next_url = sociallogin.state.get("next") or request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return next_url
        return None

    def _create_signup_from_google(self, email, extra_data):
        """First-time Google sign-in for this email - create a Signup row.

        New Google accounts default to role='user'. Parking owners still
        go through the normal owner sign-up + document verification flow;
        they can switch to Google login later once their email matches.
        """
        first_name = extra_data.get("given_name") or (extra_data.get("name", "").split(" ")[0] if extra_data.get("name") else "Parkify")
        last_name = extra_data.get("family_name") or ""

        user = Signup.objects.create(
            first_name=first_name,
            last_name=last_name,
            username=self._unique_username(email, extra_data),
            email=email,
            # Random unusable password - this account only ever signs in
            # via Google unless the person later uses "Forgot password".
            password=make_password(get_random_string(32)),
            role="user",
        )

        self._attach_google_picture(user, extra_data.get("picture"))
        return user

    def _unique_username(self, email, extra_data):
        base = email.split("@")[0] or extra_data.get("name", "user")
        base = "".join(ch for ch in base.lower() if ch.isalnum() or ch in ("_", ".", "-"))
        base = base or "user"

        username = base
        suffix = 1
        while Signup.objects.filter(username=username).exists():
            suffix += 1
            username = f"{base}{suffix}"
        return username

    def _attach_google_picture(self, user, picture_url):
        """Best-effort: pull the Google avatar in as the profile photo.
        Never blocks account creation if this fails."""
        if not picture_url:
            return
        try:
            import requests
            resp = requests.get(picture_url, timeout=5)
            resp.raise_for_status()
            user.profile_image.save(f"google_{user.id}.jpg", ContentFile(resp.content), save=True)
        except Exception:
            pass