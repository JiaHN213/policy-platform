from django.db import migrations


def seed_existing(apps, schema_editor):
    Event = apps.get_model("policies", "PublicationEvent")
    Consumption = apps.get_model("policies", "PublicationConsumption")
    for event in Event.objects.using(schema_editor.connection.alias).iterator(chunk_size=200):
        consumers = (
            ["subscription", "statistics"]
            if event.kind.startswith("opportunity.deadline.")
            else ["search", "subscription", "wiki", "statistics"]
        )
        for consumer in consumers:
            done = consumer == "subscription" and event.delivered_at is not None
            Consumption.objects.using(schema_editor.connection.alias).get_or_create(
                event_id=event.pk,
                consumer=consumer,
                defaults={
                    "status": "succeeded" if done else "pending",
                    "succeeded_at": event.delivered_at if done else None,
                    "last_success_version": event.policy_version if done else None,
                    "result": {"message": "原有通知已处理，保留处理结果，不重复发送。"}
                    if done
                    else {},
                },
            )


class Migration(migrations.Migration):
    dependencies = [("policies", "0016_publication_consumers")]
    operations = [migrations.RunPython(seed_existing, migrations.RunPython.noop)]
