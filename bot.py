import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands


BOT_NAME = "Transione"
STAFF_USER_ID_ENV = "NIKUKIER_USER_ID"
ORDERS_FILE = Path("orders.json")

STATUS_PENDING = "AGUARDANDO"
STATUS_ACCEPTED = "ACEITA"
STATUS_RECEIVED = "RECEBIDA"
STATUS_REJECTED = "RECUSADA"
STATUS_IN_PROGRESS = "EM ANDAMENTO"
STATUS_DELIVERED = "ENTREGUE"
DISPLAY_TIMEZONE = ZoneInfo("America/Bahia")

LEGACY_STATUS_MAP = {
    "solicitada": STATUS_PENDING,
    "aguardando confirmação": STATUS_PENDING,
    "aguardando": STATUS_PENDING,
    "aceita": STATUS_ACCEPTED,
    "recebida": STATUS_RECEIVED,
    "recusada": STATUS_REJECTED,
    "em andamento": STATUS_IN_PROGRESS,
    "finalizada": STATUS_DELIVERED,
    "entregue": STATUS_DELIVERED,
}

STATUS_LABELS = {
    STATUS_PENDING: "Aguardando",
    STATUS_ACCEPTED: "Aceita",
    STATUS_RECEIVED: "Recebida",
    STATUS_REJECTED: "Recusada",
    STATUS_IN_PROGRESS: "Em andamento",
    STATUS_DELIVERED: "Entregue",
}

STATUS_COLORS = {
    STATUS_PENDING: discord.Color.blurple(),
    STATUS_ACCEPTED: discord.Color.green(),
    STATUS_RECEIVED: discord.Color.blue(),
    STATUS_REJECTED: discord.Color.red(),
    STATUS_IN_PROGRESS: discord.Color.orange(),
    STATUS_DELIVERED: discord.Color.dark_green(),
}

STATUS_BY_ACTION = {
    "accept": STATUS_ACCEPTED,
    "reject": STATUS_REJECTED,
    "received": STATUS_RECEIVED,
    "progress": STATUS_IN_PROGRESS,
    "delivered": STATUS_DELIVERED,
}

ALLOWED_TRANSITIONS = {
    STATUS_PENDING: {STATUS_ACCEPTED, STATUS_REJECTED},
    STATUS_ACCEPTED: {STATUS_RECEIVED, STATUS_IN_PROGRESS, STATUS_DELIVERED},
    STATUS_RECEIVED: {STATUS_IN_PROGRESS, STATUS_DELIVERED},
    STATUS_IN_PROGRESS: {STATUS_DELIVERED},
    STATUS_REJECTED: set(),
    STATUS_DELIVERED: set(),
}

STATUS_BUTTONS = {
    "accept": {
        "label": "Aceitar",
        "emoji": "✅",
        "style": discord.ButtonStyle.success,
    },
    "reject": {
        "label": "Recusar",
        "emoji": "❌",
        "style": discord.ButtonStyle.danger,
    },
    "received": {
        "label": "Recebida",
        "emoji": "📥",
        "style": discord.ButtonStyle.primary,
    },
    "progress": {
        "label": "Em andamento",
        "emoji": "🚚",
        "style": discord.ButtonStyle.primary,
    },
    "delivered": {
        "label": "Entregue",
        "emoji": "📦",
        "style": discord.ButtonStyle.success,
    },
}

DISCORD_LOCATION_HOSTS = {
    "discord.com",
    "www.discord.com",
    "canary.discord.com",
    "ptb.discord.com",
}

CATALOG_SERVICES = [
    (
        "🚚 Transporte dedicado",
        "Veículo exclusivo para sua carga e sua rota.",
    ),
    (
        "📦 Carga fracionada",
        "Envios menores com aproveitamento de rota.",
    ),
    (
        "🗺️ Rotas personalizadas",
        "Planejamento de origem, destino, data e horário.",
    ),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(BOT_NAME)


def load_orders() -> dict[str, dict[str, Any]]:
    """Load saved orders so order numbers survive bot restarts."""
    if not ORDERS_FILE.exists():
        return {}

    try:
        with ORDERS_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return {}
        for order in data.values():
            normalize_order(order)
        return data
    except (OSError, json.JSONDecodeError) as error:
        logger.error("Não foi possível ler %s: %s", ORDERS_FILE, error)
        return {}


def normalize_status(status: Any) -> str:
    normalized_status = str(status or "").strip().casefold()
    return LEGACY_STATUS_MAP.get(normalized_status, STATUS_PENDING)


def normalize_order(order: Any) -> None:
    if not isinstance(order, dict):
        return
    order["status"] = normalize_status(order.get("status"))
    order.setdefault("origin_link", order.get("origin", ""))
    order.setdefault("destination_link", order.get("destination", ""))
    order.setdefault("recipient_user_id", order.get("requester_id"))
    order.setdefault("recipient_provided", False)
    history = order.get("status_history")
    if not isinstance(history, dict):
        history = {}
    if not history:
        initial_timestamp = order.get("created_at") or order.get("updated_at")
        if initial_timestamp:
            history[order["status"]] = initial_timestamp
    order["status_history"] = history


def save_orders(orders: dict[str, dict[str, Any]]) -> None:
    """Write orders atomically to avoid corrupting the JSON on interruption."""
    temporary_file = ORDERS_FILE.with_suffix(".tmp")
    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(orders, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temporary_file.replace(ORDERS_FILE)


def next_order_number(orders: dict[str, dict[str, Any]]) -> str:
    highest_number = 0
    for order_number in orders:
        if order_number.startswith("TRN-"):
            try:
                highest_number = max(highest_number, int(order_number.removeprefix("TRN-")))
            except ValueError:
                continue
    return f"TRN-{highest_number + 1:04d}"


def get_staff_user_id() -> int:
    raw_user_id = os.getenv(STAFF_USER_ID_ENV, "").strip()
    if not raw_user_id:
        raise RuntimeError(
            f"A variável de ambiente {STAFF_USER_ID_ENV} não está configurada."
        )
    try:
        user_id = int(raw_user_id)
    except ValueError as error:
        raise RuntimeError(
            f"A variável {STAFF_USER_ID_ENV} deve conter apenas o ID numérico do Discord."
        ) from error
    if user_id <= 0:
        raise RuntimeError(f"A variável {STAFF_USER_ID_ENV} deve ser um ID positivo.")
    return user_id


def format_datetime(value: str) -> str:
    """Keep the user-provided date/time readable without assuming a timezone."""
    return value.strip() or "Não informado"


def format_status_timestamp(value: str | None) -> str:
    if not value:
        return "não registrado"
    try:
        timestamp = datetime.fromisoformat(value).astimezone(DISPLAY_TIMEZONE)
        return timestamp.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value


def is_valid_discord_location_link(value: str) -> bool:
    clean_value = value.strip()
    parsed = urlparse(clean_value)
    return (
        parsed.scheme == "https"
        and parsed.netloc.casefold() in DISCORD_LOCATION_HOSTS
        and parsed.path.startswith("/channels/")
    )


def location_link(value: str, fallback: str = "Não informado") -> str:
    clean_value = value.strip()
    if is_valid_discord_location_link(clean_value):
        return f"[Abrir localização]({clean_value})"
    return clean_value or fallback


def split_status_timestamp(value: str | None) -> tuple[str, str]:
    formatted = format_status_timestamp(value)
    if " " in formatted:
        date_value, time_value = formatted.split(" ", maxsplit=1)
        return date_value, time_value
    return formatted, "não registrado"


def split_trip_datetime(value: str) -> tuple[str, str]:
    clean_value = " ".join(value.strip().split())
    if not clean_value:
        return "Não informado", "Não informado"
    date_value, separator, time_value = clean_value.rpartition(" ")
    if separator and ":" in time_value:
        date_value = date_value.removesuffix("às").removesuffix("as").strip()
        return date_value or clean_value, time_value
    return clean_value, "Não informado"


def status_history_text(order: dict[str, Any]) -> str:
    history = order.get("status_history", {})
    history_items = [
        (STATUS_PENDING, "Aguardando"),
        (STATUS_ACCEPTED, "Aceita"),
        (STATUS_RECEIVED, "Recebida"),
        (STATUS_REJECTED, "Recusada"),
        (STATUS_IN_PROGRESS, "Em andamento"),
        (STATUS_DELIVERED, "Entregue"),
    ]
    lines = [
        f"**{label} em:** {format_status_timestamp(history.get(status))}"
        for status, label in history_items
        if status in history
    ]
    return "\n".join(lines) or "Nenhuma atualização registrada."


def build_order_embed(order_number: str, order: dict[str, Any]) -> discord.Embed:
    normalize_order(order)
    status = order["status"]

    embed = discord.Embed(
        title=f"Solicitação de viagem · {order_number}",
        description="Solicitação recebida pela Transione.",
        color=STATUS_COLORS[status],
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Código TRN", value=f"**{order_number}**", inline=False)
    embed.add_field(
        name="Usuário que solicitou",
        value=f"{order['requester_name']} (<@{order['requester_id']}>)",
        inline=True,
    )
    embed.add_field(
        name="ID do usuário",
        value=f"`{order['requester_id']}`",
        inline=True,
    )
    recipient_id = order.get("recipient_user_id", order["requester_id"])
    recipient_text = (
        f"<@{recipient_id}>"
        if order.get("recipient_provided")
        else "Próprio solicitante"
    )
    embed.add_field(name="Destinatário", value=recipient_text, inline=True)
    embed.add_field(name="ID do destinatário", value=f"`{recipient_id}`", inline=True)
    embed.add_field(
        name="📍 Origem",
        value=location_link(order.get("origin_link", ""), order.get("origin", "")),
        inline=True,
    )
    embed.add_field(
        name="📍 Destino",
        value=location_link(order.get("destination_link", ""), order.get("destination", "")),
        inline=True,
    )
    embed.add_field(
        name="Data",
        value=format_datetime(order["date"]),
        inline=True,
    )
    embed.add_field(
        name="Horário",
        value=format_datetime(order["time"]),
        inline=True,
    )
    embed.add_field(name="Carga", value=order["cargo_description"], inline=False)
    embed.add_field(name="Status atual", value=f"**{status}**", inline=False)
    embed.add_field(name="Histórico de status", value=status_history_text(order), inline=False)
    if order.get("delivery_notification_warning"):
        embed.add_field(
            name="⚠️ Aviso de notificação",
            value=order["delivery_notification_warning"],
            inline=False,
        )
    embed.set_footer(text="Transione · Gestão de transporte")
    return embed


def build_client_confirmation_embed(
    order_number: str,
    order: dict[str, Any],
) -> discord.Embed:
    embed = discord.Embed(
        title="Solicitação enviada",
        description="Recebemos os dados da sua viagem.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Número do pedido", value=f"**{order_number}**", inline=False)
    embed.add_field(
        name="📍 Origem",
        value=location_link(order["origin_link"], order.get("origin", "")),
        inline=True,
    )
    embed.add_field(
        name="📍 Destino",
        value=location_link(order["destination_link"], order.get("destination", "")),
        inline=True,
    )
    embed.add_field(name="Data", value=order["date"], inline=True)
    embed.add_field(name="Horário", value=order["time"], inline=True)
    embed.add_field(name="Carga", value=order["cargo_description"], inline=False)
    embed.add_field(
        name="Status",
        value=f"**{order['status']}**",
        inline=False,
    )
    embed.set_footer(text="Você será avisado por mensagem privada quando o status mudar.")
    return embed


def build_status_notification_embed(
    order_number: str,
    order: dict[str, Any],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"Atualização do pedido {order_number}",
        description="O status da sua solicitação foi atualizado.",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Número do pedido", value=f"**{order_number}**", inline=False)
    embed.add_field(
        name="Novo status",
        value=f"**{order['status']}**",
        inline=False,
    )
    embed.add_field(
        name="📍 Origem",
        value=location_link(order["origin_link"], order.get("origin", "")),
        inline=True,
    )
    embed.add_field(
        name="📍 Destino",
        value=location_link(order["destination_link"], order.get("destination", "")),
        inline=True,
    )
    embed.add_field(name="Data", value=order["date"], inline=True)
    embed.add_field(name="Horário", value=order["time"], inline=True)
    updated_at = order.get("status_history", {}).get(order["status"])
    embed.add_field(
        name="Horário da atualização",
        value=format_status_timestamp(updated_at),
        inline=False,
    )
    embed.set_footer(text="Transione · Gestão de transporte")
    return embed


def build_delivery_notification_embed(
    order_number: str,
    order: dict[str, Any],
) -> discord.Embed:
    delivery_date, delivery_time = split_status_timestamp(
        order.get("status_history", {}).get(STATUS_DELIVERED)
    )
    embed = discord.Embed(
        title="📦 TRANSIONE — SUA ENCOMENDA CHEGOU",
        description=(
            "Sua encomenda já foi entregue e está disponível.\n"
            "Por favor, dirija-se ao local indicado para buscá-la."
        ),
        color=discord.Color.dark_green(),
    )
    embed.add_field(name="Pedido", value=f"**{order_number}**", inline=True)
    embed.add_field(name="Status", value=f"**{STATUS_DELIVERED}**", inline=True)
    embed.add_field(
        name="📍 Local para retirada",
        value=location_link(order["destination_link"], order.get("destination", "")),
        inline=False,
    )
    embed.add_field(name="Data da entrega", value=delivery_date, inline=True)
    embed.add_field(name="Horário da entrega", value=delivery_time, inline=True)
    embed.set_footer(text="Transione · Gestão de transporte")
    return embed


def build_recipient_delivery_confirmation_embed(
    order_number: str,
    order: dict[str, Any],
) -> discord.Embed:
    recipient_id = order["recipient_user_id"]
    embed = build_status_notification_embed(order_number, order)
    embed.title = f"Pedido {order_number} entregue"
    embed.description = (
        f"O destinatário <@{recipient_id}> foi avisado para realizar a retirada."
    )
    return embed


class MainPanelView(discord.ui.View):
    def __init__(self, bot: "TransioneBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Ver Catálogo",
        style=discord.ButtonStyle.secondary,
        emoji="📦",
        custom_id="transione:catalog",
    )
    async def catalog_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        embed = discord.Embed(
            title="Catálogo Transione",
            description="Soluções para transportar sua carga com segurança e previsibilidade.",
            color=discord.Color.blurple(),
        )
        for name, description in CATALOG_SERVICES:
            embed.add_field(name=name, value=description, inline=False)
        embed.set_footer(text="Para solicitar uma viagem, use o botão abaixo.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="Solicitar Viagem",
        style=discord.ButtonStyle.primary,
        emoji="🚚",
        custom_id="transione:request",
    )
    async def request_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(TravelRequestModal(self.bot))


class TravelRequestModal(discord.ui.Modal, title="Solicitar viagem"):
    origin = discord.ui.TextInput(
        label="Link da origem",
        placeholder="https://discord.com/channels/...",
        max_length=200,
        required=True,
    )
    destination = discord.ui.TextInput(
        label="Link do destino",
        placeholder="https://discord.com/channels/...",
        max_length=200,
        required=True,
    )
    trip_datetime = discord.ui.TextInput(
        label="Data e horário",
        placeholder="Ex.: 15/09/2026 às 08:30",
        max_length=60,
        required=True,
    )
    cargo_description = discord.ui.TextInput(
        label="O que será transportado",
        placeholder="Informe tipo, quantidade, peso e observações importantes.",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )
    recipient_user_id = discord.ui.TextInput(
        label="ID do destinatário (opcional)",
        placeholder="Preencha apenas se for para outra pessoa; caso contrário, deixe vazio.",
        max_length=20,
        required=False,
    )

    def __init__(self, bot: "TransioneBot"):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        origin_link = self.origin.value.strip()
        destination_link = self.destination.value.strip()
        invalid_links = [
            label
            for label, value in (
                ("origem", origin_link),
                ("destino", destination_link),
            )
            if not is_valid_discord_location_link(value)
        ]
        if invalid_links:
            await interaction.followup.send(
                "Informe links válidos do Discord para "
                + " e ".join(invalid_links)
                + ". Use o formato https://discord.com/channels/...",
                ephemeral=True,
            )
            return

        recipient_raw = self.recipient_user_id.value.strip()
        recipient_id = interaction.user.id
        recipient_provided = bool(recipient_raw)
        recipient_user: discord.User | None = None
        if recipient_provided:
            if not recipient_raw.isdigit() or int(recipient_raw) <= 0:
                await interaction.followup.send(
                    "O ID do destinatário deve conter apenas números.",
                    ephemeral=True,
                )
                return
            recipient_id = int(recipient_raw)
            try:
                recipient_user = self.bot.get_user(recipient_id)
                if recipient_user is None:
                    recipient_user = await self.bot.fetch_user(recipient_id)
            except (discord.NotFound, discord.HTTPException):
                await interaction.followup.send(
                    "Não encontrei um usuário Discord com esse ID de destinatário.",
                    ephemeral=True,
                )
                return

        try:
            staff_user_id = self.bot.get_staff_user_id()
        except RuntimeError:
            logger.error("Configuração do atendente ausente ou inválida.")
            await interaction.followup.send(
                f"A configuração {STAFF_USER_ID_ENV} ainda não foi definida. "
                "Avise o administrador do bot.",
                ephemeral=True,
            )
            return

        async with self.bot.orders_lock:
            order_number = next_order_number(self.bot.orders)
            created_at = datetime.now(timezone.utc).isoformat()
            date_value, time_value = split_trip_datetime(self.trip_datetime.value)
            order = {
                "requester_id": interaction.user.id,
                "requester_name": str(interaction.user),
                "origin": origin_link,
                "destination": destination_link,
                "origin_link": origin_link,
                "destination_link": destination_link,
                "date": date_value,
                "time": time_value,
                "cargo_description": self.cargo_description.value.strip(),
                "recipient_user_id": recipient_id,
                "recipient_provided": recipient_provided,
                "recipient_name": str(recipient_user) if recipient_user else str(interaction.user),
                "status": STATUS_PENDING,
                "created_at": created_at,
                "status_history": {STATUS_PENDING: created_at},
            }
            self.bot.orders[order_number] = order
            save_orders(self.bot.orders)

        try:
            staff_user = self.bot.get_user(staff_user_id)
            if staff_user is None:
                staff_user = await self.bot.fetch_user(staff_user_id)
            staff_message = await staff_user.send(
                embed=build_order_embed(order_number, order),
                view=OrderStatusView(self.bot, order_number, staff_user_id, STATUS_PENDING),
            )
        except (discord.Forbidden, discord.NotFound):
            self.bot.orders.pop(order_number, None)
            save_orders(self.bot.orders)
            await interaction.followup.send(
                "Não consegui encontrar ou enviar a solicitação para o usuário "
                "configurado em NIKUKIER_USER_ID. Verifique o ID e as mensagens diretas.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as error:
            logger.exception("Falha ao enviar o pedido %s: %s", order_number, error)
            self.bot.orders.pop(order_number, None)
            save_orders(self.bot.orders)
            await interaction.followup.send(
                "Ocorreu um erro ao encaminhar sua solicitação. Tente novamente.",
                ephemeral=True,
            )
            return

        order["staff_user_id"] = staff_user_id
        order["staff_message_id"] = staff_message.id
        order["staff_channel_id"] = staff_message.channel.id
        save_orders(self.bot.orders)

        await interaction.followup.send(
            embed=build_client_confirmation_embed(order_number, order),
            ephemeral=True,
        )


class OrderStatusView(discord.ui.View):
    def __init__(
        self,
        bot: "TransioneBot",
        order_number: str,
        staff_user_id: int,
        status: str,
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.order_number = order_number
        self.staff_user_id = staff_user_id
        self.status = normalize_status(status)

        visible_actions = {
            STATUS_PENDING: ("accept", "reject"),
            STATUS_ACCEPTED: ("received", "progress", "delivered"),
            STATUS_RECEIVED: ("progress", "delivered"),
            STATUS_IN_PROGRESS: ("delivered",),
            STATUS_REJECTED: ("accept", "reject", "received", "progress", "delivered"),
            STATUS_DELIVERED: ("accept", "reject", "received", "progress", "delivered"),
        }.get(self.status, ())
        terminal_status = self.status in {STATUS_REJECTED, STATUS_DELIVERED}

        for action in visible_actions:
            button_config = STATUS_BUTTONS[action]
            button = discord.ui.Button(
                label=button_config["label"],
                style=button_config["style"],
                emoji=button_config["emoji"],
                custom_id=f"transione:order:{action}",
                disabled=terminal_status,
            )
            button.callback = self.handle_button
            self.add_item(button)

    async def handle_button(self, interaction: discord.Interaction) -> None:
        custom_id = str(interaction.data.get("custom_id", ""))
        action = custom_id.rsplit(":", maxsplit=1)[-1]
        target_status = STATUS_BY_ACTION.get(action)
        if target_status is None:
            await interaction.response.send_message(
                "Ação de status inválida.",
                ephemeral=True,
            )
            return
        await self.change_status(interaction, target_status)

    async def change_status(self, interaction: discord.Interaction, status: str) -> None:
        try:
            configured_staff_user_id = self.bot.get_staff_user_id()
        except RuntimeError:
            await interaction.response.send_message(
                f"A configuração {STAFF_USER_ID_ENV} está ausente ou inválida.",
                ephemeral=True,
            )
            return

        if interaction.user.id != configured_staff_user_id:
            await interaction.response.send_message(
                "Somente o usuário configurado em NIKUKIER_USER_ID pode atualizar este pedido.",
                ephemeral=True,
            )
            return

        if status not in ALLOWED_TRANSITIONS.get(self.status, set()):
            await interaction.response.send_message(
                f"Não é possível mudar de **{STATUS_LABELS.get(self.status, self.status)}** "
                f"para **{STATUS_LABELS.get(status, status)}**.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        async with self.bot.orders_lock:
            order = self.bot.orders.get(self.order_number)
            if order is None:
                await interaction.followup.send(
                    "Este pedido não foi encontrado no histórico do bot.",
                    ephemeral=True,
                )
                return
            normalize_order(order)
            old_status = order["status"]
            if status not in ALLOWED_TRANSITIONS.get(old_status, set()):
                await interaction.followup.send(
                    f"Este pedido já está em **{STATUS_LABELS.get(old_status, old_status)}** "
                    "e não pode voltar para uma etapa anterior.",
                    ephemeral=True,
                )
                return

            updated_at = datetime.now(timezone.utc).isoformat()
            order["status"] = status
            order["updated_at"] = updated_at
            order.setdefault("status_history", {})[status] = updated_at
            save_orders(self.bot.orders)

        next_view = OrderStatusView(
            self.bot,
            self.order_number,
            configured_staff_user_id,
            status,
        )
        if interaction.message is not None:
            await interaction.message.edit(
                embed=build_order_embed(self.order_number, order),
                view=next_view,
            )

        if status == STATUS_DELIVERED:
            recipient_id = order.get("recipient_user_id", order["requester_id"])
            recipient = self.bot.get_user(recipient_id)
            delivery_warning = None
            if recipient is None:
                try:
                    recipient = await self.bot.fetch_user(recipient_id)
                except (discord.NotFound, discord.HTTPException):
                    recipient = None

            if recipient is None:
                delivery_warning = (
                    "⚠️ Não foi possível avisar o destinatário por mensagem privada."
                )
            else:
                try:
                    await recipient.send(
                        embed=build_delivery_notification_embed(
                            self.order_number,
                            order,
                        )
                    )
                except (discord.Forbidden, discord.HTTPException):
                    delivery_warning = (
                        "⚠️ Não foi possível avisar o destinatário por mensagem privada."
                    )

            async with self.bot.orders_lock:
                if delivery_warning:
                    order["delivery_notification_warning"] = delivery_warning
                else:
                    order.pop("delivery_notification_warning", None)
                save_orders(self.bot.orders)

            if delivery_warning and interaction.message is not None:
                await interaction.message.edit(
                    embed=build_order_embed(self.order_number, order),
                    view=next_view,
                )

            if recipient_id != order["requester_id"]:
                requester = self.bot.get_user(order["requester_id"])
                if requester is None:
                    try:
                        requester = await self.bot.fetch_user(order["requester_id"])
                    except (discord.NotFound, discord.HTTPException):
                        requester = None
                if requester is not None:
                    try:
                        await requester.send(
                            embed=build_recipient_delivery_confirmation_embed(
                                self.order_number,
                                order,
                            )
                        )
                    except (discord.Forbidden, discord.HTTPException):
                        logger.warning(
                            "Não foi possível avisar o solicitante %s por DM.",
                            order["requester_id"],
                        )
        elif old_status != status:
            requester = self.bot.get_user(order["requester_id"])
            if requester is None:
                try:
                    requester = await self.bot.fetch_user(order["requester_id"])
                except (discord.NotFound, discord.HTTPException):
                    requester = None

            if requester is not None:
                try:
                    await requester.send(
                        embed=build_status_notification_embed(
                            self.order_number,
                            order,
                        )
                    )
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning(
                        "Não foi possível avisar o solicitante %s por DM.",
                        order["requester_id"],
                    )

        if interaction.message is not None:
            self.bot.add_view(next_view, message_id=interaction.message.id)

        await interaction.followup.send(
            f"Pedido {self.order_number} atualizado para **{status}**.",
            ephemeral=True,
        )


class TransioneBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        # Slash commands and component interactions do not require message
        # content or members intents. The configured Discord ID is fetched
        # directly through the API.
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.orders = load_orders()
        self.orders_lock = asyncio.Lock()
        self.synced = False
        self.order_views_restored = False

    async def setup_hook(self) -> None:
        self.add_view(MainPanelView(self))

    async def on_ready(self) -> None:
        if not self.synced:
            await self.tree.sync()
            self.synced = True
        if not self.order_views_restored:
            await self.restore_order_views()
            self.order_views_restored = True
        logger.info("Bot conectado como %s", self.user)
        logger.info("Pedidos carregados: %d", len(self.orders))

    async def restore_order_views(self) -> None:
        restored = 0
        for order_number, order in self.orders.items():
            channel_id = order.get("staff_channel_id")
            message_id = order.get("staff_message_id")
            staff_user_id = order.get("staff_user_id")
            if not all((channel_id, message_id, staff_user_id)):
                continue

            normalize_order(order)
            view = OrderStatusView(
                self,
                order_number,
                int(staff_user_id),
                order["status"],
            )
            try:
                channel = self.get_channel(int(channel_id))
                if channel is None:
                    channel = await self.fetch_channel(int(channel_id))
                message = await channel.fetch_message(int(message_id))
                await message.edit(
                    embed=build_order_embed(order_number, order),
                    view=view,
                )
            except (discord.Forbidden, discord.HTTPException, AttributeError) as error:
                logger.warning(
                    "Não foi possível atualizar a View do pedido %s: %s",
                    order_number,
                    error,
                )
            self.add_view(view, message_id=int(message_id))
            restored += 1
        logger.info("Controles restaurados para %d pedido(s).", restored)

    def get_staff_user_id(self) -> int:
        return get_staff_user_id()


def create_panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Transione",
        description=(
            "Olá! Somos sua ponte para uma logística simples, segura e eficiente.\n\n"
            "Consulte nosso catálogo ou envie os detalhes da sua próxima viagem."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Como funciona?",
        value="1. Consulte as opções\n2. Informe sua rota e carga\n3. Acompanhe o status do pedido",
        inline=False,
    )
    embed.set_footer(text="Transione · Transporte sob medida")
    return embed


bot = TransioneBot()


@bot.tree.command(name="painel", description="Exibe o painel de atendimento da Transione.")
async def panel_command(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        embed=create_panel_embed(),
        view=MainPanelView(bot),
    )


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "A variável de ambiente DISCORD_TOKEN não está configurada. "
            "Adicione o token em Secrets antes de iniciar o bot."
        )
    get_staff_user_id()
    bot.run(token)


if __name__ == "__main__":
    main()