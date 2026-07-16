from django.contrib import admin
from .models import Signup, Booking,OwnerProfile, OwnerDocument, ParkingLot, PaymentTransaction, Review, Notification
# Register your models here.

admin.site.register(Signup)
admin.site.register(Booking)
admin.site.register(OwnerProfile)
admin.site.register(OwnerDocument)
admin.site.register(ParkingLot)
admin.site.register(PaymentTransaction)
admin.site.register(Notification)


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('id', 'parking', 'user', 'rating', 'created_at', 'has_reply')
    list_filter = ('rating', 'created_at')
    search_fields = ('parking__parking_name', 'user__username', 'comment')

    def has_reply(self, obj):
        return bool(obj.owner_reply)
    has_reply.boolean = True
    has_reply.short_description = 'Owner Replied'