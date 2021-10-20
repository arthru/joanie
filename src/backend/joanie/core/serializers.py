"""Serializers for api."""

from django.conf import settings
from django.core.cache import cache

from rest_framework import serializers

from joanie.core import enums, models


class CertificationDefinitionSerializer(serializers.ModelSerializer):
    """
    Serialize information about a certificate definition
    """

    description = serializers.CharField(read_only=True)

    class Meta:
        model = models.CertificateDefinition
        fields = ["description", "name", "title"]
        read_only_fields = ["description", "name", "title"]


class OrganizationSerializer(serializers.ModelSerializer):
    """
    Serialize all non-sensitive information about an organization
    """

    class Meta:
        model = models.Organization
        fields = ["code", "title"]
        read_only_fields = ["code", "title"]


class TargetCourseSerializer(serializers.ModelSerializer):
    """
    Serialize all information about a target course.
    """

    course_runs = serializers.SerializerMethodField(read_only=True)
    organization = OrganizationSerializer(read_only=True)
    position = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.Course
        fields = [
            "code",
            "course_runs",
            "organization",
            "position",
            "title",
        ]
        read_only_fields = [
            "code",
            "course_runs",
            "organization",
            "position",
            "title",
        ]

    def get_position(self, target_course):
        """
        Retrieve the position of the course related to its product_relation
        """
        product = self.context.get("product", None)
        return (
            target_course.product_relations.only("position")
            .get(product=product)
            .position
        )

    @staticmethod
    def get_course_runs(target_course):
        """
        Return related course runs ordered by start date asc
        """
        return CourseRunSerializer(
            target_course.course_runs.order_by("start"), many=True
        ).data


class ProductSerializer(serializers.ModelSerializer):
    """
    Product serializer including
        - certificate information if there is
        - targeted courses with its course runs
            - If user is authenticated, we try to retrieve enrollment related
              to each course run.
        - order if user is authenticated
    """

    id = serializers.CharField(read_only=True, source="uid")
    certificate = CertificationDefinitionSerializer(
        read_only=True, source="certificate_definition"
    )
    currency = serializers.SerializerMethodField(read_only=True)
    price = serializers.DecimalField(
        coerce_to_string=False,
        decimal_places=2,
        max_digits=9,
        min_value=0,
        read_only=True,
    )
    target_courses = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.Product
        fields = [
            "call_to_action",
            "certificate",
            "currency",
            "id",
            "price",
            "target_courses",
            "title",
            "type",
        ]
        read_only_fields = [
            "call_to_action",
            "certificate",
            "currency",
            "id",
            "price",
            "target_courses",
            "title",
            "type",
        ]

    def get_target_courses(self, product):
        """
        For the current product, retrieve its related courses.
        """

        context = self.context.copy()
        context.update({"product": product})

        return TargetCourseSerializer(
            instance=models.Course.objects.filter(
                product_relations__product=product
            ).order_by("product_relations__position"),
            many=True,
            context=context,
        ).data

    def get_currency(self, _):  # pylint: disable=no-self-use
        """
        Return currency code and symbol
        to allow frontend to format properly price
        """
        (code, symbol) = settings.JOANIE_CURRENCY[:2]
        return {"code": code, "symbol": symbol}


class CourseRunEnrollmentSerializer(serializers.ModelSerializer):
    """
    Enrollment for course run serializer
    """

    id = serializers.CharField(source="uid", read_only=True, required=False)
    resource_link = serializers.CharField(
        read_only=True, source="course_run.resource_link"
    )
    title = serializers.CharField(read_only=True, source="course_run.title")
    start = serializers.DateTimeField(read_only=True, source="course_run.start")
    end = serializers.DateTimeField(read_only=True, source="course_run.end")
    enrollment_start = serializers.DateTimeField(
        read_only=True, source="course_run.enrollment_start"
    )
    enrollment_end = serializers.DateTimeField(
        read_only=True, source="course_run.enrollment_end"
    )

    class Meta:
        model = models.Enrollment
        fields = [
            "id",
            "is_active",
            "resource_link",
            "title",
            "start",
            "end",
            "enrollment_start",
            "enrollment_end",
            "state",
        ]
        read_only_fields = [
            "id",
            "is_active",
            "resource_link",
            "title",
            "start",
            "end",
            "enrollment_start",
            "enrollment_end",
            "state",
        ]


class OrderLiteSerializer(serializers.ModelSerializer):
    """
    Minimal Order model serializer
    """

    id = serializers.CharField(read_only=True, source="uid")
    price = serializers.DecimalField(
        coerce_to_string=False,
        decimal_places=2,
        max_digits=9,
        min_value=0,
        read_only=True,
    )
    enrollments = CourseRunEnrollmentSerializer(
        many=True, read_only=True, required=False
    )
    product = serializers.SlugRelatedField(read_only=True, slug_field="uid")

    class Meta:
        model = models.Order
        fields = [
            "id",
            "created_on",
            "price",
            "enrollments",
            "product",
            "state",
        ]
        read_only_fields = [
            "id",
            "created_on",
            "price",
            "enrollments",
            "product",
            "state",
        ]


class CourseSerializer(serializers.ModelSerializer):
    """
    Serialize all information about a course.
    """

    organization = OrganizationSerializer(read_only=True)
    products = ProductSerializer(many=True, read_only=True)

    class Meta:
        model = models.Course
        fields = [
            "code",
            "organization",
            "title",
            "products",
        ]
        read_only_fields = [
            "code",
            "organization",
            "title",
            "products",
        ]

    def get_orders(self, instance):
        """
        If an user is authenticated, retrieves its orders related to the serializer Course instance
        else return None
        """
        try:
            username = self.context["username"]
            orders = (
                models.Order.objects.filter(
                    owner__username=username,
                    course=instance,
                    state__in=[
                        enums.ORDER_STATE_FAILED,
                        enums.ORDER_STATE_FINISHED,
                        enums.ORDER_STATE_PAID,
                        enums.ORDER_STATE_PENDING,
                    ],
                )
                .select_related("product")
                .prefetch_related("enrollments__course_run")
            )

            return OrderLiteSerializer(orders, many=True).data
        except KeyError:
            return None

    def to_representation(self, instance):
        """
        Cache the serializer representation that does not vary from user to user
        then, if user is authenticated, add private information to the representation
        """
        cache_key = instance.get_cache_key()
        representation = cache.get(cache_key)

        if representation is None:
            representation = super().to_representation(instance)
            cache.set(
                cache_key,
                representation,
                settings.JOANIE_ANONYMOUS_COURSE_SERIALIZER_CACHE_TTL,
            )

        representation["orders"] = self.get_orders(instance)

        return representation


class CourseRunSerializer(serializers.ModelSerializer):
    """
    Serialize all information about a course run
    """

    class Meta:
        model = models.CourseRun
        fields = [
            "end",
            "enrollment_end",
            "enrollment_start",
            "id",
            "resource_link",
            "start",
            "title",
        ]
        read_only_fields = [
            "end",
            "enrollment_end",
            "enrollment_start",
            "id",
            "resource_link",
            "start",
            "title",
        ]


class EnrollmentSerializer(serializers.ModelSerializer):
    """
    Enrollment model serializer
    """

    id = serializers.CharField(source="uid", read_only=True, required=False)
    user = serializers.CharField(source="user.username", read_only=True, required=False)
    course_run = serializers.SlugRelatedField(
        queryset=models.CourseRun.objects.all(), slug_field="resource_link"
    )
    order = serializers.SlugRelatedField(
        queryset=models.Order.objects.all(), slug_field="uid", required=False
    )

    class Meta:
        model = models.Enrollment
        fields = ["id", "user", "course_run", "order", "is_active", "state"]
        read_only_fields = ["id", "user", "state"]

    def update(self, instance, validated_data):
        """
        Restrict the values that can be set from the API for the state field to "set".
        The "failed" state can only be set by the LMSHandler.
        """
        validated_data.pop("course_run", None)
        validated_data.pop("order", None)
        return super().update(instance, validated_data)


class OrderSerializer(serializers.ModelSerializer):
    """
    Order model serializer
    """

    id = serializers.CharField(source="uid", read_only=True, required=False)
    owner = serializers.CharField(
        source="owner.username", read_only=True, required=False
    )
    course = serializers.SlugRelatedField(
        queryset=models.Course.objects.all(), slug_field="code"
    )
    price = serializers.DecimalField(
        coerce_to_string=False,
        decimal_places=2,
        max_digits=9,
        min_value=0,
        read_only=True,
        required=False,
    )
    product = serializers.SlugRelatedField(
        queryset=models.Product.objects.all(), slug_field="uid"
    )
    enrollments = CourseRunEnrollmentSerializer(
        many=True, read_only=True, required=False
    )
    target_courses = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = models.Order
        fields = [
            "course",
            "created_on",
            "enrollments",
            "id",
            "owner",
            "price",
            "product",
            "state",
            "target_courses",
        ]
        read_only_fields = [
            "created_on",
            "enrollments",
            "id",
            "owner",
            "price",
            "state",
            "target_courses",
        ]

    @staticmethod
    def get_target_courses(obj):
        """Compute the serialized value for the "target_courses" field."""
        return (
            models.Course.objects.filter(order_relations__order=obj)
            .order_by("order_relations__position")
            .values_list("code", flat=True)
        )

    def update(self, instance, validated_data):
        """Make the "course" and "product" fields read_only only on update."""
        validated_data.pop("course", None)
        validated_data.pop("product", None)
        return super().update(instance, validated_data)


class AddressSerializer(serializers.ModelSerializer):
    """
    Address model serializer
    """

    id = serializers.CharField(source="uid", read_only=True, required=False)

    class Meta:
        model = models.Address
        fields = [
            "address",
            "city",
            "country",
            "first_name",
            "last_name",
            "id",
            "is_main",
            "postcode",
            "title",
        ]
        read_only_fields = [
            "id",
        ]
