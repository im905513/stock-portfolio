#!/bin/bash
ssh -i /home/openclaw/.ssh/rd_dev_server ubuntu@192.168.88.174 "cd ~/stock-portfolio && python3 update_prices.py" >> /home/openclaw/.openclaw/workspace-goldman-agent/logs/price_update.log 2>&1
