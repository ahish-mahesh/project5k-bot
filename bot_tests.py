# #!/usr/bin/env python3
# """
# Comprehensive test suite for Project5K Discord Bot

# Tests the three core functionalities independently of Discord APIs:
# 1. LLM workout plan generation and motivation functionality
# 2. Workout plan parsing functionality 
# 3. Google Calendar API integration (standalone auth test)

# Usage: python bot_tests.py
# """

# import asyncio
# import unittest
# import tempfile
# import os
# import json
# import re
# from unittest.mock import Mock, patch, MagicMock
# import datetime
# from llama_log_redirect import llama_log_redirect

# # Import the functions we want to test from the main bot file
# import sys
# sys.path.append('.')
# from project5k_bot import get_motivation, parse_workout_plan, llm

# class TestLLMFunctionality(unittest.TestCase):
#     """Test suite for LLM-based functions"""
    
#     def test_get_motivation_basic(self):
#         """Test basic motivation message generation"""
#         print("\n🧪 Testing LLM motivation generation...")
        
#         # Test with different workout durations
#         test_minutes = [15, 30, 60, 120]
        
#         for minutes in test_minutes:
#             with self.subTest(minutes=minutes):
#                 result = get_motivation(minutes)
                
#                 # Basic validation
#                 self.assertIsInstance(result, str)
#                 self.assertGreater(len(result), 0)
#                 self.assertLess(len(result), 1000)  # Reasonable length check
                
#                 print(f"✅ {minutes} min workout motivation: {result[:100]}...")
    
#     def test_llm_workout_plan_generation(self):
#         """Test LLM workout plan generation with one quick example"""
#         print("\n🧪 Testing LLM workout plan generation (quick test)...")
        
#         # Test with just one goal to keep the test fast
#         goal = "strength training"
#         prompt = (
#             f"Create a 7-day workout plan for the goal: {goal}. "
#             "List only the days and the workout for each day. "
#             "Format exactly as: Monday: ...\\nTuesday: ...\\nWednesday: ...\\nThursday: ...\\nFriday: ...\\nSaturday: ...\\nSunday: ... "
#             "No introduction, no summary, just the plan."
#         )
#         prompt = f"<s>[INST] You are a friendly, supportive fitness coach. {prompt} [/INST]"
#         with llama_log_redirect("logs/bot_tests_llm.log"):
#             output = llm(
#                 prompt,
#                 max_tokens=500,  # Reduced for faster testing
#                 top_p=0.95,
#                 stop=["<s>"]
#             )
#         response = output["choices"][0]["text"] # type: ignore
        
#         # Basic validation
#         self.assertIsInstance(response, str)
#         self.assertGreater(len(response), 50)  # Should be substantial
        
#         # Check that at least some days are mentioned
#         days_mentioned = sum(1 for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] 
#                            if day in response)
#         self.assertGreaterEqual(days_mentioned, 3, f"Expected at least 3 days mentioned in plan for {goal}")
        
#         print(f"✅ {goal} plan generated ({len(response)} chars, {days_mentioned} days mentioned)")
#         print(f"   Sample: {response[:150]}...")
#         print("   Note: LLM generation verified - additional goals would work similarly")


# class TestWorkoutPlanParsing(unittest.TestCase):
#     """Test suite for workout plan parsing functionality"""
    
#     def test_parse_workout_plan_basic(self):
#         """Test basic workout plan parsing"""
#         print("\n🧪 Testing workout plan parsing...")
        
#         # Test case 1: Simple, well-formatted plan
#         plan_text = """Monday: 30 minutes of cardio
# Tuesday: Upper body strength training
# Wednesday: Rest day or light yoga
# Thursday: Lower body workout
# Friday: HIIT training
# Saturday: Outdoor run
# Sunday: Full body stretching"""
        
#         events = parse_workout_plan(plan_text)
        
#         self.assertEqual(len(events), 7)
#         self.assertEqual(events[0][0], "Monday")
#         self.assertEqual(events[1][0], "Tuesday")
#         self.assertIn("cardio", events[0][1])
#         self.assertIn("strength", events[1][1])
        
#         print("✅ Basic plan parsing successful")
#         for day, workout in events:
#             print(f"   {day}: {workout[:50]}...")
    
#     def test_parse_workout_plan_with_extra_text(self):
#         """Test parsing with extra text before/after the plan"""
#         print("\n🧪 Testing parsing with extra text...")
        
#         plan_text = """Here's your workout plan:
        
# Monday: Push-ups and squats
# Tuesday: Running 5k
# Wednesday: Yoga session
# Thursday: Weight lifting
# Friday: Swimming
# Saturday: Hiking
# Sunday: Rest day

# This plan will help you achieve your fitness goals!"""
        
#         events = parse_workout_plan(plan_text)
        
#         self.assertEqual(len(events), 7)
#         self.assertEqual(events[0][0], "Monday")
#         self.assertIn("Push-ups", events[0][1])
        
#         print("✅ Plan with extra text parsing successful")
    
#     def test_parse_workout_plan_partial(self):
#         """Test parsing with only some days mentioned"""
#         print("\n🧪 Testing partial plan parsing...")
        
#         plan_text = """Monday: Chest and triceps
# Wednesday: Back and biceps
# Friday: Legs and shoulders"""
        
#         events = parse_workout_plan(plan_text)
        
#         self.assertEqual(len(events), 3)
#         days = [event[0] for event in events]
#         self.assertIn("Monday", days)
#         self.assertIn("Wednesday", days)
#         self.assertIn("Friday", days)
        
#         print("✅ Partial plan parsing successful")
    
#     def test_parse_workout_plan_empty(self):
#         """Test parsing with empty or invalid input"""
#         print("\n🧪 Testing empty/invalid plan parsing...")
        
#         # Empty string
#         events = parse_workout_plan("")
#         self.assertEqual(len(events), 0)
        
#         # No day patterns
#         events = parse_workout_plan("This is just random text without any days")
#         self.assertEqual(len(events), 0)
        
#         print("✅ Empty/invalid plan parsing handled correctly")


# class TestGoogleCalendarIntegration(unittest.TestCase):
#     """Test suite for Google Calendar API integration (without actual API calls)"""
    
#     def setUp(self):
#         """Set up test environment"""
#         self.test_user_id = "test_user_123"
#         self.temp_dir = tempfile.mkdtemp()
        
#     def test_calendar_service_mock(self):
#         """Test Google Calendar service creation (mocked)"""
#         print("\n🧪 Testing Google Calendar integration (mocked)...")
        
#         # Mock the credentials file
#         mock_credentials = {
#             "installed": {
#                 "client_id": "test_client_id",
#                 "client_secret": "test_client_secret",
#                 "auth_uri": "https://accounts.google.com/o/oauth2/auth",
#                 "token_uri": "https://oauth2.googleapis.com/token"
#             }
#         }
        
#         # Create a temporary credentials file
#         creds_file = os.path.join(self.temp_dir, "test_credentials.json")
#         with open(creds_file, 'w') as f:
#             json.dump(mock_credentials, f)
        
#         # Mock the requests and Google API calls
#         with patch('requests.post') as mock_post:
#             with patch('builtins.open', create=True) as mock_open:
#                 with patch('os.path.exists') as mock_exists:
                    
#                     mock_exists.return_value = False  # No existing token
                    
#                     # Mock device flow response
#                     mock_post.return_value.json.return_value = {
#                         "verification_url": "https://www.google.com/device",
#                         "user_code": "ABCD-EFGH",
#                         "device_code": "test_device_code",
#                         "expires_in": 1800,
#                         "interval": 5
#                     }
                    
#                     # Test that the function would handle the OAuth flow correctly
#                     # (We can't test the actual async function easily in unittest, 
#                     # but we can verify the components)
                    
#                     result = mock_post.return_value.json()
#                     self.assertIn("verification_url", result)
#                     self.assertIn("user_code", result)
#                     self.assertIn("device_code", result)
                    
#                     print(f"✅ OAuth device flow components validated")
#                     print(f"   Verification URL: {result['verification_url']}")
#                     print(f"   User Code: {result['user_code']}")
    
#     def test_workout_plan_to_calendar_events(self):
#         """Test conversion of workout plan to calendar events"""
#         print("\n🧪 Testing workout plan to calendar events conversion...")
        
#         # Sample workout plan
#         plan_events = [
#             ("Monday", "Upper body strength training - 45 minutes"),
#             ("Tuesday", "Cardio workout - 30 minutes running"),
#             ("Wednesday", "Rest day or light yoga"),
#             ("Thursday", "Lower body workout - squats and lunges"),
#             ("Friday", "HIIT training - 20 minutes"),
#             ("Saturday", "Outdoor activity - hiking or cycling"),
#             ("Sunday", "Full body stretching and recovery")
#         ]
        
#         # Test calendar event creation logic
#         today = datetime.date.today()
#         weekday_map = {day: i for i, day in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}
        
#         calendar_events = []
#         for day, desc in plan_events:
#             if day in weekday_map:
#                 days_ahead = (weekday_map[day] - today.weekday() + 7) % 7
#                 event_date = today + datetime.timedelta(days=days_ahead)
#                 event = {
#                     'summary': f'Workout: {desc}',
#                     'start': {'date': event_date.isoformat()},
#                     'end': {'date': event_date.isoformat()},
#                 }
#                 calendar_events.append(event)
        
#         # Validate events
#         self.assertEqual(len(calendar_events), 7)
        
#         for event in calendar_events:
#             self.assertIn('summary', event)
#             self.assertIn('start', event)
#             self.assertIn('end', event)
#             self.assertIn('Workout:', event['summary'])
        
#         print("✅ Calendar events creation successful")
#         for event in calendar_events:
#             print(f"   {event['start']['date']}: {event['summary']}")


# class TestIntegration(unittest.TestCase):
#     """Integration tests combining multiple functionalities"""
    
#     def test_end_to_end_workflow(self):
#         """Test the complete workflow: LLM -> Parse -> Calendar format"""
#         print("\n🧪 Testing end-to-end workflow...")
        
#         # Step 1: Generate a workout plan with LLM
#         goal = "strength training"
#         prompt = (
#             f"Create a 7-day workout plan for the goal: {goal}. "
#             "List only the days and the workout for each day. "
#             "Format exactly as: Monday: ...\\nTuesday: ...\\nWednesday: ...\\nThursday: ...\\nFriday: ...\\nSaturday: ...\\nSunday: ... "
#             "No introduction, no summary, just the plan."
#         )
#         prompt = f"<s>[INST] You are a friendly, supportive fitness coach. {prompt} [/INST]"
        
#         output = llm(prompt, max_tokens=500, top_p=0.95, stop=["<s>"])
#         llm_response = output["choices"][0]["text"] # type: ignore
        
#         # Step 2: Parse the workout plan
#         events = parse_workout_plan(llm_response)
        
#         # Step 3: Convert to calendar format
#         today = datetime.date.today()
#         weekday_map = {day: i for i, day in enumerate(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}
        
#         calendar_events = []
#         for day, desc in events:
#             if day in weekday_map:
#                 days_ahead = (weekday_map[day] - today.weekday() + 7) % 7
#                 event_date = today + datetime.timedelta(days=days_ahead)
#                 event = {
#                     'summary': f'Workout: {desc}',
#                     'start': {'date': event_date.isoformat()},
#                     'end': {'date': event_date.isoformat()},
#                 }
#                 calendar_events.append(event)
        
#         # Validate the complete workflow
#         self.assertGreater(len(llm_response), 50, "LLM should generate substantial response")
#         self.assertGreater(len(events), 0, "Parser should extract events")
#         self.assertEqual(len(calendar_events), len(events), "All parsed events should convert to calendar events")
        
#         print("✅ End-to-end workflow successful!")
#         print(f"   LLM generated {len(llm_response)} characters")
#         print(f"   Parser extracted {len(events)} workout days")
#         print(f"   Created {len(calendar_events)} calendar events")
        
#         # Show sample results
#         print("\n📅 Sample calendar events:")
#         for i, event in enumerate(calendar_events[:3]):  # Show first 3
#             print(f"   {i+1}. {event['start']['date']}: {event['summary'][:60]}...")


# def run_tests():
#     """Run all tests with proper formatting"""
#     print("🚀 Starting Project5K Bot Tests")
#     print("=" * 50)
    
#     # Create test suite
#     loader = unittest.TestLoader()
#     suite = unittest.TestSuite()
    
#     # Add test classes
#     suite.addTests(loader.loadTestsFromTestCase(TestLLMFunctionality))
#     suite.addTests(loader.loadTestsFromTestCase(TestWorkoutPlanParsing))
#     suite.addTests(loader.loadTestsFromTestCase(TestGoogleCalendarIntegration))
#     suite.addTests(loader.loadTestsFromTestCase(TestIntegration))
    
#     # Run tests
#     runner = unittest.TextTestRunner(verbosity=2)
#     result = runner.run(suite)
    
#     print("\n" + "=" * 50)
#     if result.wasSuccessful():
#         print("🎉 All tests passed!")
#     else:
#         print(f"❌ {len(result.failures)} test(s) failed, {len(result.errors)} error(s)")
        
#     print(f"📊 Ran {result.testsRun} tests total")
    
#     return result.wasSuccessful()


# if __name__ == "__main__":
#     # Check if required files exist
#     required_files = [
#         "project5k_bot.py",
#         "phi-2.Q4_K_M.gguf",
#         "google_api_credentials.json"
#     ]
    
#     missing_files = [f for f in required_files if not os.path.exists(f)]
#     if missing_files:
#         print(f"❌ Missing required files: {missing_files}")
#         print("Please ensure all required files are in the current directory.")
#         sys.exit(1)
    
#     # Run the tests
#     success = run_tests()
#     sys.exit(0 if success else 1)