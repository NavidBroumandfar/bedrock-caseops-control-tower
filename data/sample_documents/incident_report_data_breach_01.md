# Title: Incident Report — Customer Data Exposure (Synthetic)

Source: Synthetic incident report derived from public breach notification patterns
Date: 2026-03-01

## Incident Summary

An internal security review identified that a misconfigured cloud storage bucket had exposed a subset of customer records to public read access for an estimated 11-day window. The exposed data included names, email addresses, and hashed (bcrypt) passwords for approximately 12,500 accounts. No payment card data, social security numbers, or plaintext passwords were exposed.

## Discovery

The misconfiguration was identified by an automated configuration compliance scan on 2026-02-28. No evidence of external data access was found in access logs, but the exposure window cannot be fully excluded given the nature of the misconfiguration.

## Root Cause

A storage bucket created for a new analytics pipeline was provisioned with a misconfigured access control policy that defaulted to public read. The bucket was created without following the standard provisioning runbook, which requires a security review for any new storage resource.

## Impact

- 12,500 customer accounts had email, name, and bcrypt-hashed password exposed
- Exposure window: 2026-02-17 to 2026-02-28 (estimated 11 days)
- No payment or identity document data involved
- No confirmed unauthorized access based on available logs

## Corrective Actions

- Bucket access control corrected immediately upon discovery
- All affected accounts flagged for forced password reset notification
- Internal security team conducting full audit of all storage bucket access controls
- Provisioning runbook updated to enforce mandatory security review gate
- Legal and privacy teams notified; regulatory notification assessment underway

This document represents a data exposure incident report with potential regulatory notification implications. Core signals:
- customer data exposure via misconfiguration
- personal data involved (names, email, hashed passwords)
- no confirmed exfiltration but cannot be excluded
- regulatory and legal assessment required
- corrective actions underway
