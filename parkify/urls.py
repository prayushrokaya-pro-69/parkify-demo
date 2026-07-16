from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('authentication/', views.authentication, name='authentication'),
    path('admin-dashboard/',views.admin_dashboard,name='admin_dashboard'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('owner-dashboard/', views.owner_dashboard, name='owner_dashboard'),

    path('logout/', views.logout_view, name='logout'),

    # ---- Forgot Password (custom flow against the Signup model) ----
    path('forgot-password/', views.forgot_password, name='forgot_password'),
    path('forgot-password/sent/', views.password_reset_done, name='password_reset_done'),
    path('reset/<uidb64>/<token>/', views.reset_password_confirm, name='password_reset_confirm'),
    path('reset/done/', views.password_reset_complete, name='password_reset_complete'),

    path('my-bookings/',views.my_bookings,name='my_bookings'),
    path('user-profile/',views.user_profile,name='user_profile'),
    path('owner-profile/',views.owner_profile,name='owner_profile'),
    path('owner-document/',views.owner_document,name='owner_document'), 
    path('approve-owner/<int:owner_id>/',views.approve_owner,name='approve_owner'),
    path('reject-owner/<int:owner_id>/',views.reject_owner,name='reject_owner'),
    path('add-parking/',views.add_parking,name='add_parking'),
    path('my-parking-lots/',views.my_parking_lots,name='my_parking_lots'),
    path('parking/<int:parking_id>/',views.view_parking,name='view_parking'),
    path('parking/edit/<int:parking_id>/',views.edit_parking,name='edit_parking'),
    path('parking/delete/<int:parking_id>/',views.delete_parking,name='delete_parking'),

    # ---- Search & Booking module ----
    path('browse-parking/', views.browse_parking, name='browse_parking'),
    path('parking/<int:parking_id>/book/', views.book_parking, name='book_parking'),
    path('map/', views.map_search, name='map_search'),
    path('booking/cancel/<int:booking_id>/', views.cancel_booking, name='cancel_booking'),

    # ---- Payment module ----
    path('payment/<int:booking_id>/', views.payment_page, name='payment_page'),
    path('payment/<int:booking_id>/process/', views.process_payment, name='process_payment'),
    path('payment/receipt/<str:txn_id>/', views.payment_receipt, name='payment_receipt'),

    # ---- Review & Rating module ----
    path('booking/<int:booking_id>/review/', views.submit_review, name='submit_review'),
    path('review/<int:review_id>/edit/', views.edit_review, name='edit_review'),
    path('review/<int:review_id>/delete/', views.delete_review, name='delete_review'),
    path('parking/<int:parking_id>/reviews/', views.parking_reviews, name='parking_reviews'),
    path('review/<int:review_id>/reply/', views.owner_reply_review, name='owner_reply_review'),

    # ---- Owner notifications ----
    path('notifications/<int:notification_id>/read/', views.mark_notification_read, name='mark_notification_read'),
    path('notifications/mark-all-read/', views.mark_all_notifications_read, name='mark_all_notifications_read'),
    path('notifications/clear/', views.clear_notifications, name='clear_notifications'),

    # ---- Owner: manage bookings for their own parking lots ----
    path('owner-dashboard/booking/<int:booking_id>/update/', views.owner_update_booking, name='owner_update_booking'),
    path('admin-dashboard/parking/<int:parking_id>/toggle/', views.admin_toggle_parking, name='admin_toggle_parking'),
    path('admin-dashboard/parking/<int:parking_id>/delete/', views.admin_delete_parking, name='admin_delete_parking'),
    path('admin-dashboard/review/<int:review_id>/delete/', views.admin_delete_review, name='admin_delete_review'),

    # ---- Change password ----
    path("change-password/",views.change_password,name="change_password"),
    path("verify-otp/", views.verify_otp, name="verify_otp"),
    path("reactivate-account/", views.reactivate_account, name="reactivate_account"),
    path("two-factor/toggle/", views.two_factor_toggle, name="two_factor_toggle"),
    path("account/delete/", views.delete_account, name="delete_account"),

    # ---- Saved locations ----
    path("saved-locations/", views.saved_locations_list, name="saved_locations_list"),
    path("saved-locations/<int:parking_id>/toggle/", views.saved_locations_toggle, name="saved_locations_toggle"),
    path("saved-locations/<int:saved_id>/remove/", views.saved_location_remove, name="saved_location_remove"),
]