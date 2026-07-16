from django.db import models
class Signup(models.Model):
    ROLE_CHOICES = (
        ('user', 'User'),
        ('owner', 'Owner'),
        ('admin', 'Admin'),
    )

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    username = models.CharField(max_length=100, unique=True)
    email = models.EmailField(unique=True)
    password = models.CharField(max_length=255)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    profile_image = models.ImageField(upload_to='profile_images/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    two_factor_enabled = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.username} ({self.role})"


# One-time codes for email-based two-factor login verification and account reactivation
class OTPCode(models.Model):

    PURPOSE_CHOICES = (
        ('login', 'Login 2FA'),
        ('reactivation', 'Account Reactivation'),
    )

    user = models.ForeignKey(Signup, on_delete=models.CASCADE, related_name='otp_codes')
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default='login')
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def is_expired(self):
        from django.utils import timezone
        from datetime import timedelta
        return timezone.now() > self.created_at + timedelta(minutes=10)

    def __str__(self):
        return f"OTP for {self.user.username}"
    

#owner profile 
class OwnerProfile(models.Model):

    owner = models.OneToOneField(Signup,on_delete=models.CASCADE)

    full_name = models.CharField(max_length=150)

    company_name = models.CharField(max_length=150)

    registration_no = models.CharField(max_length=100)

    phone = models.CharField(max_length=20)

    address = models.TextField()

    is_verified = models.BooleanField(default=False)

    def __str__(self):
        return self.full_name
  #owner document    
class OwnerDocument(models.Model):

    owner = models.ForeignKey(OwnerProfile,on_delete=models.CASCADE)

    document_id = models.CharField(max_length=100)
    
    document_type = models.CharField(max_length=100)

    file_url = models.FileField(upload_to='documents/')

    upload_date = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=20,default='Pending')

    def __str__(self):
        return self.document_type 


# Parking Lot
class ParkingLot(models.Model):
    owner = models.ForeignKey(OwnerProfile,on_delete=models.CASCADE)

    parking_name = models.CharField(max_length=150)

    parking_image = models.ImageField(upload_to='parking_images/',blank=True,null=True)

    location = models.CharField(max_length=255)
    latitude = models.DecimalField(max_digits=9,decimal_places=6,null=True,blank=True)
    longitude = models.DecimalField(max_digits=9,decimal_places=6,null=True,blank=True)

    car_capacity = models.IntegerField()

    bike_capacity = models.IntegerField()

    rate_per_hour = models.DecimalField(max_digits=10,decimal_places=2)

    map_link = models.URLField(blank=True, null=True)

    description = models.TextField()

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def average_rating(self):
        result = self.reviews.aggregate(avg=models.Avg('rating'))['avg']
        return round(result, 1) if result else 0

    def review_count(self):
        return self.reviews.count()


 # Saved Locations
class SavedLocation(models.Model):

    user = models.ForeignKey(Signup, on_delete=models.CASCADE, related_name='saved_locations')

    parking = models.ForeignKey(ParkingLot, on_delete=models.CASCADE, related_name='saved_by_users')

    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'parking')
        ordering = ['-saved_at']

    def __str__(self):
        return f"{self.user.username} saved {self.parking.parking_name}"


 #Bookings module   
class Booking(models.Model):

    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Active', 'Active'),
        ('Completed', 'Completed'),
        ('Cancelled', 'Cancelled')
    ]

    PAYMENT_CHOICES = [
        ('Paid', 'Paid'),
        ('Unpaid', 'Unpaid')
    ]

    user = models.ForeignKey(Signup,on_delete=models.CASCADE)

    parking_name = models.CharField(max_length=150)

    vehicle_number = models.CharField(max_length=30)

    vehicle_type = models.CharField(max_length=20)

    booking_date = models.DateField()

    check_in = models.TimeField()

    check_out = models.TimeField()

    duration = models.IntegerField()

    amount = models.DecimalField(max_digits=10,decimal_places=2)

    payment_status = models.CharField(max_length=20,choices=PAYMENT_CHOICES)

    status = models.CharField(max_length=20,choices=STATUS_CHOICES,default='Pending')

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Booking #{self.id}"


# Payment module
class PaymentTransaction(models.Model):

    METHOD_CHOICES = [
        ('Card', 'Credit/Debit Card'),
        ('Esewa', 'eSewa'),
        ('Khalti', 'Khalti'),
    ]

    STATUS_CHOICES = [
        ('Success', 'Success'),
        ('Failed', 'Failed'),
    ]

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='transactions')

    txn_id = models.CharField(max_length=40, unique=True)

    method = models.CharField(max_length=20, choices=METHOD_CHOICES)

    amount = models.DecimalField(max_digits=10, decimal_places=2)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Success')

    paid_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.txn_id} - {self.status}"


# Review & Rating module
class Review(models.Model):

    RATING_CHOICES = [
        (5, '5 - Excellent'),
        (4, '4 - Good'),
        (3, '3 - Average'),
        (2, '2 - Poor'),
        (1, '1 - Terrible'),
    ]

    parking = models.ForeignKey(
        ParkingLot, on_delete=models.CASCADE, related_name='reviews'
    )

    user = models.ForeignKey(
        Signup, on_delete=models.CASCADE, related_name='reviews'
    )

    # One review per completed booking - also stops a user reviewing
    # a lot they never actually parked at.
    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, related_name='review'
    )

    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)

    comment = models.TextField(blank=True)

    owner_reply = models.TextField(blank=True, null=True)
    replied_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.rating}★ by {self.user.username} on {self.parking.parking_name}"


# Owner notifications - fired on new bookings and new reviews
class Notification(models.Model):

    TYPE_CHOICES = [
        ('booking', 'Booking'),
        ('review', 'Review'),
    ]

    recipient = models.ForeignKey(
        Signup, on_delete=models.CASCADE, related_name='notifications'
    )

    notif_type = models.CharField(max_length=20, choices=TYPE_CHOICES)

    title = models.CharField(max_length=150)
    message = models.TextField()

    # Relative URL to send the owner to when they click the notification.
    link = models.CharField(max_length=255, blank=True)

    is_read = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.notif_type}] {self.title} -> {self.recipient.username}"