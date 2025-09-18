"""
WhatsApp Notification Service

Handles sending WhatsApp notifications for new leads with duplicate prevention.
Only sends notifications once per lead and respects environment flags.
"""

import os
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from app.services.firebase_service import get_firestore_client
from app.services.baileys_service import baileys_service
from app.config.lawyers import get_lawyers_for_notification, format_lawyer_phone_for_whatsapp

logger = logging.getLogger(__name__)


async def send_new_lead_notification(lead_id: str, lead_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Send WhatsApp notification for a new lead (only once per lead).
    
    Args:
        lead_id (str): ID of the lead
        lead_data (Dict[str, Any]): Lead data from the conversation
        
    Returns:
        Dict[str, Any]: Notification result
    """
    try:
        # Check if WhatsApp notifications are enabled
        whatsapp_enabled = os.getenv("ENABLE_WHATSAPP", "false").lower() == "true"
        if not whatsapp_enabled:
            logger.info(f"📵 WhatsApp notifications disabled (ENABLE_WHATSAPP=false) for lead {lead_id}")
            return {
                "success": False,
                "reason": "whatsapp_disabled",
                "message": "WhatsApp notifications are disabled via environment variable"
            }
        
        # Check if lead was already notified
        db = get_firestore_client()
        lead_ref = db.collection("leads").document(lead_id)
        lead_doc = lead_ref.get()
        
        if not lead_doc.exists:
            logger.error(f"❌ Lead {lead_id} not found in database")
            return {
                "success": False,
                "reason": "lead_not_found",
                "message": f"Lead {lead_id} not found"
            }
        
        lead_record = lead_doc.to_dict()
        
        # Check if already notified
        if lead_record.get("was_notified", False):
            logger.info(f"📵 Lead {lead_id} already notified, skipping duplicate notification")
            return {
                "success": False,
                "reason": "already_notified",
                "message": f"Lead {lead_id} was already notified"
            }
        
        # Extract lead information
        answers = lead_data.get("answers", [])
        lead_name = "Cliente não identificado"
        lead_phone = "Não informado"
        area = "Não informada"
        situation = "Não detalhada"
        
        # Parse answers to extract information
        for answer in answers:
            answer_id = answer.get("id", 0)
            answer_text = answer.get("answer", "")
            
            if answer_id == 1:  # Name
                lead_name = answer_text
            elif answer_id == 2:  # Area
                area = answer_text
            elif answer_id == 3:  # Situation
                situation = answer_text
            elif answer_id >= 4 and answer_text.isdigit():  # Phone (usually last numeric answer)
                lead_phone = answer_text
        
        logger.info(f"📨 Sending WhatsApp notification for new lead: {lead_name} ({area})")
        
        # Send notifications to all lawyers
        notification_result = await _send_notifications_to_lawyers(
            lead_id, lead_name, lead_phone, area, situation
        )
        
        # Mark as notified if at least one notification was sent successfully
        if notification_result.get("notifications_sent", 0) > 0:
            try:
                lead_ref.update({
                    "was_notified": True,
                    "notified_at": datetime.now(timezone.utc),
                    "notification_result": notification_result,
                    "updated_at": datetime.now(timezone.utc)
                })
                logger.info(f"✅ Lead {lead_id} marked as notified")
            except Exception as update_error:
                logger.error(f"❌ Error updating notification status: {str(update_error)}")
        
        return notification_result
        
    except Exception as e:
        logger.error(f"❌ Error in send_new_lead_notification: {str(e)}")
        return {
            "success": False,
            "reason": "error",
            "message": str(e),
            "notifications_sent": 0
        }


async def _send_notifications_to_lawyers(
    lead_id: str,
    lead_name: str,
    lead_phone: str,
    area: str,
    situation: str
) -> Dict[str, Any]:
    """
    Send notifications to all configured lawyers.
    
    Args:
        lead_id (str): Lead ID
        lead_name (str): Client name
        lead_phone (str): Client phone
        area (str): Legal area
        situation (str): Client situation
        
    Returns:
        Dict[str, Any]: Notification results
    """
    try:
        lawyers = get_lawyers_for_notification()
        results = []
        successful_notifications = 0
        
        # Create notification message
        notification_message = f"""🚨 *Novo Cliente Recebido!*

👤 *Nome:* {lead_name}
📞 *Telefone:* {lead_phone}
⚖️ *Área:* {area}
📝 *Situação:* {situation[:200]}{'...' if len(situation) > 200 else ''}

🆔 *Lead ID:* {lead_id}
⏰ *Recebido em:* {datetime.now().strftime('%d/%m/%Y às %H:%M')}

_Mensagem enviada automaticamente pelo sistema de captação de leads._"""
        
        for lawyer in lawyers:
            try:
                lawyer_name = lawyer.get("name", "Advogado")
                lawyer_phone = lawyer.get("phone", "")
                
                if not lawyer_phone:
                    logger.warning(f"⚠️ No phone number for lawyer {lawyer_name}")
                    continue
                
                # Format phone for WhatsApp
                whatsapp_number = format_lawyer_phone_for_whatsapp(lawyer_phone)
                
                # Send notification
                success = await baileys_service.send_whatsapp_message(
                    whatsapp_number,
                    notification_message
                )
                
                results.append({
                    "lawyer": lawyer_name,
                    "phone": lawyer_phone,
                    "success": success,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                
                if success:
                    successful_notifications += 1
                    logger.info(f"✅ Notification sent to {lawyer_name}")
                else:
                    logger.error(f"❌ Failed to send notification to {lawyer_name}")
                    
            except Exception as lawyer_error:
                logger.error(f"❌ Error sending notification to {lawyer.get('name', 'Unknown')}: {str(lawyer_error)}")
                results.append({
                    "lawyer": lawyer.get("name", "Unknown"),
                    "phone": lawyer.get("phone", "Unknown"),
                    "success": False,
                    "error": str(lawyer_error),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
        
        return {
            "success": successful_notifications > 0,
            "notifications_sent": successful_notifications,
            "total_lawyers": len(lawyers),
            "results": results,
            "lead_id": lead_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error sending notifications to lawyers: {str(e)}")
        return {
            "success": False,
            "notifications_sent": 0,
            "error": str(e)
        }


async def check_notification_status(lead_id: str) -> Dict[str, Any]:
    """
    Check if a lead has been notified.
    
    Args:
        lead_id (str): Lead ID to check
        
    Returns:
        Dict[str, Any]: Notification status
    """
    try:
        db = get_firestore_client()
        lead_doc = db.collection("leads").document(lead_id).get()
        
        if not lead_doc.exists:
            return {
                "exists": False,
                "was_notified": False,
                "message": "Lead not found"
            }
        
        lead_data = lead_doc.to_dict()
        
        return {
            "exists": True,
            "was_notified": lead_data.get("was_notified", False),
            "notified_at": lead_data.get("notified_at"),
            "notification_result": lead_data.get("notification_result", {}),
            "lead_id": lead_id
        }
        
    except Exception as e:
        logger.error(f"❌ Error checking notification status: {str(e)}")
        return {
            "exists": False,
            "was_notified": False,
            "error": str(e)
        }


async def reset_notification_status(lead_id: str) -> bool:
    """
    Reset notification status for a lead (for testing purposes).
    
    Args:
        lead_id (str): Lead ID to reset
        
    Returns:
        bool: True if reset successfully
    """
    try:
        db = get_firestore_client()
        lead_ref = db.collection("leads").document(lead_id)
        
        lead_ref.update({
            "was_notified": False,
            "notified_at": None,
            "notification_result": None,
            "updated_at": datetime.now(timezone.utc)
        })
        
        logger.info(f"🔄 Reset notification status for lead {lead_id}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error resetting notification status: {str(e)}")
        return False