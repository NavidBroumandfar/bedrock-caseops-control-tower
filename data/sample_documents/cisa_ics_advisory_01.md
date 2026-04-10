# Title: CISA ICS Advisory — Critical Vulnerability in Industrial Control Systems

Source: https://www.cisa.gov/news-events/ics-advisories (synthetic, derived from public CISA ICS advisory patterns)
Date: 2026-01-15

This advisory describes a critical vulnerability affecting programmable logic controllers (PLCs) widely deployed in water treatment and energy distribution infrastructure.

CISA and the vendor have confirmed a remotely exploitable authentication bypass vulnerability (CVE-2026-00191) in the affected PLC firmware. An unauthenticated remote attacker who can reach the device management interface can gain full control of the device without credentials. No user interaction is required.

The vulnerability has a CVSS v3.1 base score of 9.8 (Critical). Affected versions include firmware releases prior to 4.7.2 across multiple product families deployed in water treatment facilities, electric substations, and manufacturing plants.

CISA strongly urges asset owners to:
- Apply the vendor-released firmware update (version 4.7.2 or later) immediately
- Restrict network access to PLC management interfaces using firewall rules and VPN
- Monitor for unauthorized access attempts on OT networks
- Report any signs of unauthorized device reconfiguration to CISA

This advisory reflects the type of ICS/OT security notice the pipeline must process and escalate. Core signals:
- critical CVSS score remote code execution class vulnerability
- industrial control system / operational technology
- active exploitation risk
- immediate patch required
- infrastructure sectors affected
