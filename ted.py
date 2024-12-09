import os
import discord
from discord.ext import commands
from datetime import datetime, timedelta
import requests
from requests.auth import HTTPBasicAuth

import os
from dotenv import load_dotenv  # Import dotenv to read the .env file
import discord
from discord.ext import commands

# Load environment variables from .env file
load_dotenv()

# =========================
# Configuration
# =========================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
API_KEY = os.getenv("INTERVALS_ICU_API_KEY")
OWNER_ID = int(os.environ.get("DISCORD_OWNER_ID", "YOUR_USER_ID"))  # Replace "YOUR_USER_ID" with your actual Discord user ID

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Required for fetching user objects
bot = commands.Bot(command_prefix="!", intents=intents)

API_BASE_URL = "https://intervals.icu/api/v1"
#TEST AV AUTOUPDATE!!
# TEST 2 av auto!
# =========================
# Parse ATHLETE_IDS from the environment
# =========================
ATHLETE_IDS = {}
for i in range(1, 20):  # Support up to 20 athletes (can be increased as needed)
    athlete_entry = os.getenv(f"ATHLETE_ID_{i}")
    if athlete_entry:
        athlete_id, athlete_name = athlete_entry.split("=")
        ATHLETE_IDS[athlete_id] = athlete_name

CYCLING_TYPES = {"Ride", "VirtualRide"}
INCLUDED_TYPES = CYCLING_TYPES | {"Run"}

# =========================
# Data Fetching and Processing Functions
# =========================

def get_activities(athlete_id: str, oldest_date: str, newest_date: str) -> list:
    url = f"{API_BASE_URL}/athlete/{athlete_id}/activities"
    params = {"oldest": oldest_date, "newest": newest_date}
    auth = HTTPBasicAuth('API_KEY', API_KEY)
    response = requests.get(url, auth=auth, params=params)
    if response.status_code == 200:
        return response.json()
    return []

def get_activity_power_curve(activity_id: str) -> dict:
    url = f"{API_BASE_URL}/activity/{activity_id}/power-curve"
    auth = HTTPBasicAuth('API_KEY', API_KEY)
    response = requests.get(url, auth=auth)
    if response.status_code == 200:
        return response.json()
    return {}

def get_best_effort_power(power_curve_data: dict, target_duration: int) -> float:
    secs = power_curve_data.get("secs", [])
    values = power_curve_data.get("values", [])
    if target_duration in secs:
        idx = secs.index(target_duration)
        return values[idx] if idx < len(values) else 0.0
    return 0.0

def process_activities(activities: list, weeksago: int) -> dict:
    total_distance = 0
    total_duration = 0
    total_training_load = 0
    max_normalized_power = 0
    max_avg_power = 0
    max_normalized_power_per_kg = 0
    max_avg_power_per_kg = 0
    max_pm_ftp = 0
    hr_zone_times = [0] * 5
    ctl_start = None
    ctl_end = None

    for activity in activities:
        if activity.get("type") not in INCLUDED_TYPES:
            continue

        weight = activity.get("icu_weight", 0) or 0
        ctl = activity.get("icu_ctl", None)

        if ctl is not None:
            if ctl_start is None:
                ctl_start = ctl
            ctl_end = ctl

        total_distance += activity.get("distance", 0) or 0
        total_duration += activity.get("moving_time", 0) or 0
        total_training_load += activity.get("icu_training_load", 0) or 0

        if activity.get("type") in CYCLING_TYPES:
            normalized_power = activity.get("icu_weighted_avg_watts", 0) or 0
            avg_power = activity.get("icu_average_watts", 0) or 0

            max_normalized_power = max(max_normalized_power, normalized_power)
            max_avg_power = max(max_avg_power, avg_power)

            if weight > 0:
                max_normalized_power_per_kg = max(max_normalized_power_per_kg, normalized_power / weight)
                max_avg_power_per_kg = max(max_avg_power_per_kg, avg_power / weight)

            max_pm_ftp = max(max_pm_ftp, activity.get("icu_pm_ftp", 0) or 0)

        if activity.get("icu_hr_zone_times"):
            for i, time_in_zone in enumerate(activity.get("icu_hr_zone_times", [])):
                if i < len(hr_zone_times):
                    hr_zone_times[i] += time_in_zone

    total_hr_time = sum(hr_zone_times)
    hr_zone_percentages = [
        (time / total_hr_time) * 100 if total_hr_time > 0 else 0 for time in hr_zone_times
    ]

    fitness_gain_percentage = 0.0
    if ctl_start and ctl_start > 0:
        fitness_gain_percentage = (((ctl_end - ctl_start) / ctl_start) * 100) * -1

    return {
        "total_distance": total_distance / 1000,
        "total_duration": total_duration / 3600,
        "avg_training_load_per_week": total_training_load / weeksago if weeksago > 0 else 0,
        "max_normalized_power": max_normalized_power,
        "max_avg_power": max_avg_power,
        "max_normalized_power_per_kg": max_normalized_power_per_kg,
        "max_avg_power_per_kg": max_avg_power_per_kg,
        "max_pm_ftp": max_pm_ftp,
        "hr_zone_percentages": hr_zone_percentages,
        "fitness_gain_percentage": fitness_gain_percentage
    }

def fetch_power_curves(athlete_id: str) -> dict:
    url = f"{API_BASE_URL}/athlete/{athlete_id}/power-curves"
    params = {"curves": "all", "type": "Ride"}
    auth = HTTPBasicAuth('API_KEY', API_KEY)
    response = requests.get(url, auth=auth, params=params)

    if response.status_code == 200:
        power_data = response.json().get("list", [])
        if not power_data:
            return {"best_efforts": {}, "weight": 0}

        secs_list = power_data[0]["secs"]
        values_list = power_data[0]["values"]

        best_efforts = {}
        durations = [
            (5, "5 sec"), (15, "15 sec"), (30, "30 sec"),
            (300, "5 min"), (600, "10 min"), (1200, "20 min")
        ]

        for duration, label in durations:
            candidates = [val for val, secs in zip(values_list, secs_list) if secs == duration]
            best_value = max(candidates) if candidates else 0
            best_efforts[label] = best_value

        weight = power_data[0].get("weight", 0)
        return {"best_efforts": best_efforts, "weight": weight}
    else:
        print(f"Error fetching power curves for athlete {athlete_id}: {response.status_code} - {response.text}")
        return {"best_efforts": {}, "weight": 0}


# =========================
# Formatting Functions (Now column-by-column code blocks)
# =========================

def get_personal_bests() -> str:
    athlete_data = {}
    for athlete_id, athlete_name in ATHLETE_IDS.items():
        athlete_data[athlete_name] = fetch_power_curves(athlete_id)

    columns = ["5 sec", "15 sec", "30 sec", "5 min", "10 min", "20 min"]
    max_values = {col: 0 for col in columns}
    max_values_wkg = {col: 0 for col in columns}

    for data in athlete_data.values():
        for col in columns:
            best_val = data["best_efforts"].get(col, 0)
            if best_val > max_values[col]:
                max_values[col] = best_val
            if data["weight"] > 0:
                val_wkg = best_val / data["weight"]
                if val_wkg > max_values_wkg[col]:
                    max_values_wkg[col] = val_wkg

    response = "**Personal Bests (All Time):**\n"
    # Watts
    for col in columns:
        response += "```\n"
        response += f"Athlete    | {col} (W)\n"
        response += "-" * 30 + "\n"
        for athlete_name, data in athlete_data.items():
            best_val = data["best_efforts"].get(col, 0)
            val_str = f"{best_val:.2f}"
            if best_val == max_values[col] and best_val > 0:
                val_str += "*"
            response += f"{athlete_name:<10} | {val_str:<10}\n"
        response += "```\n\n"

    # W/kg
    response += "**Additional Metrics (W/kg):**\n"
    for col in columns:
        response += "```\n"
        response += f"Athlete    | {col} (W/kg)\n"
        response += "-" * 30 + "\n"
        for athlete_name, data in athlete_data.items():
            best_val = data["best_efforts"].get(col, 0)
            weight = data["weight"]
            if weight > 0:
                val_wkg = best_val / weight
                val_str = f"{val_wkg:.2f}"
                if val_wkg == max_values_wkg[col] and val_wkg > 0:
                    val_str += "*"
            else:
                val_str = "N/A"
            response += f"{athlete_name:<10} | {val_str:<10}\n"
        response += "```\n\n"

    return response.strip()


def get_year_to_date_stats() -> str:
    start_of_year = datetime(datetime.now().year, 1, 1).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")

    ytd_stats = {}
    for athlete_id, athlete_name in ATHLETE_IDS.items():
        activities = get_activities(athlete_id, start_of_year, today)
        total_distance = sum((act.get("distance", 0) or 0) for act in activities if act.get("type") in INCLUDED_TYPES) / 1000
        total_duration = sum((act.get("moving_time", 0) or 0) for act in activities if act.get("type") in INCLUDED_TYPES) / 3600
        total_training_load = sum((act.get("icu_training_load", 0) or 0) for act in activities if act.get("type") in INCLUDED_TYPES)

        ytd_stats[athlete_name] = {
            "distance": total_distance,
            "duration": total_duration,
            "training_load": total_training_load
        }

    response = "**Year-to-Date Stats 📅:**\n"
    # Columns: Distance(km), Duration(hrs), Training Load
    # We'll show Athlete + one metric per code block

    # Distance
    response += "```\n"
    response += "Athlete    | Distance (km)\n"
    response += "-" * 30 + "\n"
    for athlete_name, data in ytd_stats.items():
        response += f"{athlete_name:<10} | {data['distance']:<10.2f}\n"
    response += "```\n\n"

    # Duration
    response += "```\n"
    response += "Athlete    | Duration (hrs)\n"
    response += "-" * 30 + "\n"
    for athlete_name, data in ytd_stats.items():
        response += f"{athlete_name:<10} | {data['duration']:<10.2f}\n"
    response += "```\n\n"

    # Training Load
    response += "```\n"
    response += "Athlete    | Training Load\n"
    response += "-" * 30 + "\n"
    for athlete_name, data in ytd_stats.items():
        response += f"{athlete_name:<10} | {data['training_load']:<10.2f}\n"
    response += "```\n"

    return response


def get_summary(weeksago: int) -> str:
    today = datetime.now()
    delta = today - timedelta(weeks=weeksago)
    oldest_date = delta.strftime("%Y-%m-%d")
    newest_date = today.strftime("%Y-%m-%d")

    athlete_comps = {}
    for athlete_id, athlete_name in ATHLETE_IDS.items():
        activities = get_activities(athlete_id, oldest_date, newest_date)
        data = process_activities(activities, weeksago)
        athlete_comps[athlete_name] = data

    # Metrics:
    # Athlete
    # Total Dist (km), Total Dur (hrs), Max Norm Pwr (W), Max Avg Pwr (W),
    # Max Norm Pwr (W/kg), Max Avg Pwr (W/kg), Max eFTP

    response = f"**Performance Summary (Last {weeksago} week(s))**\n"

    # Total Dist (km)
    response += "```\n"
    response += "Athlete    | Total Dist (km)\n"
    response += "-" * 30 + "\n"
    for ath, d in athlete_comps.items():
        response += f"{ath:<10} | {d['total_distance']:<10.2f}\n"
    response += "```\n\n"

    # Total Dur (hrs)
    response += "```\n"
    response += "Athlete    | Total Dur (hrs)\n"
    response += "-" * 30 + "\n"
    for ath, d in athlete_comps.items():
        response += f"{ath:<10} | {d['total_duration']:<10.2f}\n"
    response += "```\n\n"

    # Max Norm Pwr (W)
    response += "```\n"
    response += "Athlete    | Max Norm Pwr (W)\n"
    response += "-" * 30 + "\n"
    for ath, d in athlete_comps.items():
        response += f"{ath:<10} | {d['max_normalized_power']:<10.2f}\n"
    response += "```\n\n"

    # Max Avg Pwr (W)
    response += "```\n"
    response += "Athlete    | Max Avg Pwr (W)\n"
    response += "-" * 30 + "\n"
    for ath, d in athlete_comps.items():
        response += f"{ath:<10} | {d['max_avg_power']:<10.2f}\n"
    response += "```\n\n"

    # Max Norm Pwr (W/kg)
    response += "```\n"
    response += "Athlete    | Max Norm Pwr (W/kg)\n"
    response += "-" * 30 + "\n"
    for ath, d in athlete_comps.items():
        response += f"{ath:<10} | {d['max_normalized_power_per_kg']:<10.2f}\n"
    response += "```\n\n"

    # Max Avg Pwr (W/kg)
    response += "```\n"
    response += "Athlete    | Max Avg Pwr (W/kg)\n"
    response += "-" * 30 + "\n"
    for ath, d in athlete_comps.items():
        response += f"{ath:<10} | {d['max_avg_power_per_kg']:<10.2f}\n"
    response += "```\n\n"

    # Max eFTP
    response += "```\n"
    response += "Athlete    | Max eFTP\n"
    response += "-" * 30 + "\n"
    for ath, d in athlete_comps.items():
        response += f"{ath:<10} | {d['max_pm_ftp']:<10.2f}\n"
    response += "```\n"

    return response


def get_weekly_highlights(weeksago: int = 1) -> str:
    today = datetime.now()
    delta = today - timedelta(weeks=weeksago)
    oldest_date = delta.strftime("%Y-%m-%d")
    newest_date = today.strftime("%Y-%m-%d")

    highlights = {
        "Max 15s Power (W)": {"value": 0, "athlete": ""},
        "Max 15s Power (W/kg)": {"value": 0.0, "athlete": ""},
        "Max 20m Power (W)": {"value": 0, "athlete": ""},
        "Max 20m Power (W/kg)": {"value": 0.0, "athlete": ""},
        "Longest Duration (hrs)": {"value": 0, "athlete": ""},
        "Longest Distance (km)": {"value": 0, "athlete": ""},
        "Max % of Max HR": {"value": 0.0, "athlete": "", "hr_value": 0},
        "Most Elevation Gain (m)": {"value": 0, "athlete": ""}
    }

    for athlete_id, athlete_name in ATHLETE_IDS.items():
        activities = get_activities(athlete_id, oldest_date, newest_date)

        for activity in activities:
            if activity.get("type") not in INCLUDED_TYPES:
                continue

            power_curve_data = {}
            if activity.get("type") in CYCLING_TYPES:
                aid = activity.get("id", "")
                if aid:
                    power_curve_data = get_activity_power_curve(aid)

            max_15_sec_power = get_best_effort_power(power_curve_data, 15) if power_curve_data else 0
            max_20_min_power = get_best_effort_power(power_curve_data, 1200) if power_curve_data else 0

            weight = activity.get("icu_weight", 0) or 0
            max_15s_w_kg = (max_15_sec_power / weight) if (weight > 0 and max_15_sec_power > 0) else 0
            max_20m_w_kg = (max_20_min_power / weight) if (weight > 0 and max_20_min_power > 0) else 0

            if max_15_sec_power > highlights["Max 15s Power (W)"]["value"]:
                highlights["Max 15s Power (W)"] = {"value": max_15_sec_power, "athlete": athlete_name}

            if max_15s_w_kg > highlights["Max 15s Power (W/kg)"]["value"]:
                highlights["Max 15s Power (W/kg)"] = {"value": max_15s_w_kg, "athlete": athlete_name}

            if max_20_min_power > highlights["Max 20m Power (W)"]["value"]:
                highlights["Max 20m Power (W)"] = {"value": max_20_min_power, "athlete": athlete_name}

            if max_20m_w_kg > highlights["Max 20m Power (W/kg)"]["value"]:
                highlights["Max 20m Power (W/kg)"] = {"value": max_20m_w_kg, "athlete": athlete_name}

            single_duration = (activity.get("moving_time", 0) or 0) / 3600.0
            if single_duration > highlights["Longest Duration (hrs)"]["value"]:
                highlights["Longest Duration (hrs)"] = {"value": single_duration, "athlete": athlete_name}

            single_distance = (activity.get("distance", 0) or 0) / 1000.0
            if single_distance > highlights["Longest Distance (km)"]["value"]:
                highlights["Longest Distance (km)"] = {"value": single_distance, "athlete": athlete_name}

            athlete_max_hr = activity.get("athlete_max_hr", 0) or 0
            max_heartrate = activity.get("max_heartrate", 0) or 0
            max_hr_percent = 0.0
            if athlete_max_hr > 0 and max_heartrate > 0:
                max_hr_percent = (max_heartrate / athlete_max_hr) * 100.0

            if max_hr_percent > highlights["Max % of Max HR"]["value"]:
                highlights["Max % of Max HR"] = {"value": max_hr_percent, "athlete": athlete_name, "hr_value": max_heartrate}

            elevation_gain = activity.get("total_elevation_gain", 0) or 0
            if elevation_gain > highlights["Most Elevation Gain (m)"]["value"]:
                highlights["Most Elevation Gain (m)"] = {"value": elevation_gain, "athlete": athlete_name}

    # Each highlight in its own code block
    # Just 2 columns: Athlete and Value, since we can't horizontally scroll well.
    response = f"**Best single activity highlights last {weeksago} week(s) 📈:**\n"

    for category, data in highlights.items():
        response += "```\n"
        response += f"{'Athlete':<10} | {category}\n"
        response += "-" * 30 + "\n"
        val = data["value"]
        if category == "Max % of Max HR" and data["hr_value"] > 0:
            val_str = f"{val:.2f}({data['hr_value']}bpm)"
        else:
            val_str = f"{val:.2f}"

        response += f"{data['athlete']:<10} | {val_str:<10}\n"
        response += "```\n\n"

    return response.strip()

# =========================
# Discord Commands
# =========================

@bot.command(name="summary")
async def cmd_summary(ctx, arg):
    try:
        weeksago = int(arg)
        await ctx.send(f"Generating summary for the last {weeksago} week(s)... This may take a moment.")
        summary_text = get_summary(weeksago)
        await ctx.send(summary_text)
    except ValueError:
        await ctx.send("Please provide a valid number of weeks (e.g., !summary 1).")

@bot.command(name="highlights")
async def cmd_weekly_highlights(ctx, weeksago: int = 1):
    await ctx.send("Fetching weekly highlights...")
    response = get_weekly_highlights(weeksago)
    await ctx.send(response)

@bot.command(name="ytd")
async def cmd_year_to_date(ctx):
    await ctx.send("Calculating Year-to-Date stats...")
    response = get_year_to_date_stats()
    await ctx.send(response)

@bot.command(name="bests")
async def cmd_bests(ctx):
    await ctx.send("Fetching personal bests for all athletes... This may take a moment.")
    athlete_data = {}
    for athlete_id, athlete_name in ATHLETE_IDS.items():
        athlete_data[athlete_name] = fetch_power_curves(athlete_id)

    columns = ["5 sec", "15 sec", "30 sec", "5 min", "10 min", "20 min"]
    max_values = {col: 0 for col in columns}
    max_values_wkg = {col: 0 for col in columns}

    for data in athlete_data.values():
        for col in columns:
            best_val = data["best_efforts"].get(col, 0)
            if best_val > max_values[col]:
                max_values[col] = best_val
            if data["weight"] > 0:
                val_wkg = best_val / data["weight"]
                if val_wkg > max_values_wkg[col]:
                    max_values_wkg[col] = val_wkg

    embed = discord.Embed(title="Personal Bests (All Time)", color=discord.Color.blue())

    # Add watts table
    for col in columns:
        value_list = []
        for athlete_name, data in athlete_data.items():
            best_val = data["best_efforts"].get(col, 0)
            val_str = f"{best_val:.2f}"
            if best_val == max_values[col] and best_val > 0:
                val_str += " ⭐"
            value_list.append(f"**{athlete_name}**: {val_str}")
        embed.add_field(name=f"{col} (W)", value="\n".join(value_list), inline=False)

    # Add W/kg table
    for col in columns:
        value_list = []
        for athlete_name, data in athlete_data.items():
            best_val = data["best_efforts"].get(col, 0)
            weight = data["weight"]
            if weight > 0:
                val_wkg = best_val / weight
                val_str = f"{val_wkg:.2f}"
                if val_wkg == max_values_wkg[col] and val_wkg > 0:
                    val_str += " ⭐"
            else:
                val_str = "N/A"
            value_list.append(f"**{athlete_name}**: {val_str}")
        embed.add_field(name=f"{col} (W/kg)", value="\n".join(value_list), inline=False)

    await ctx.send(embed=embed)

# Event to notify when bot is ready
@bot.event
async def on_ready():
    """This event is triggered when the bot is online and ready."""
    print(f'✅ {bot.user.name} is now online!')
    
    # Get the user by their Discord ID
    user = await bot.fetch_user(OWNER_ID)
    if user:
        try:
            await user.send(f"🚀 **The bot is now online!**\nI'm ready to handle commands!")
            print(f"✅ DM sent to {user.name}")
        except Exception as e:
            print(f"❌ Failed to send DM: {e}")
    else:
        print("❌ User not found. Double-check the user ID.")

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send("Pong! 🏓")
    # Get the user by their Discord ID
    user = await bot.fetch_user(OWNER_ID)
    if user:
        try:
            await user.send(f"🚀 **The bot is now online!**\nI'm ready to handle commands!")
            print(f"✅ DM sent to {user.name}")
        except Exception as e:
            print(f"❌ Failed to send DM: {e}")
    else:
        print("❌ User not found. Double-check the user ID.")
# Debug prints (optional)
# print(get_weekly_highlights(1))
# print(get_year_to_date_stats())
# print(get_summary(6))
# print(get_personal_bests())

# Uncomment to run the bot
if __name__ == "__main__":
   bot.run(TOKEN)
