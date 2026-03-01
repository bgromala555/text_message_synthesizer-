/**
 * Shared type definitions for the Synthesized Chat Generator frontend.
 * These interfaces mirror the Python Pydantic models in source/models.py.
 */

export type Theme =
  | 'slice-of-life'
  | 'crime'
  | 'espionage'
  | 'romance'
  | 'family-drama'
  | 'college'
  | 'corporate'
  | 'thriller'
  | 'comedy';

export type Culture =
  | 'american'
  | 'arab-gulf'
  | 'arab-levant'
  | 'arab-north-africa'
  | 'british'
  | 'chinese'
  | 'french'
  | 'indian'
  | 'japanese'
  | 'korean'
  | 'latin-american'
  | 'nigerian'
  | 'russian'
  | 'southeast-asian'
  | 'turkish'
  | 'west-african';

export type MessageVolume = 'heavy' | 'regular' | 'light' | 'minimal';
export type SpamDensity = 'none' | 'low' | 'medium' | 'high';
export type GenerationMode = 'story' | 'standalone';
export type RoleStyle = 'normal' | 'mixed' | 'story_heavy';
export type EncounterType = 'planned' | 'chance_encounter' | 'near_miss';
export type Language = 'en' | 'es' | 'ar' | 'zh' | 'fr';
export type ToastType = 'success' | 'error' | 'info' | 'warning';

export interface TextingStyle {
  punctuation: string;
  capitalization: string;
  emoji_use: string;
  abbreviations: string;
  avg_message_length: string;
  quirks: string;
}

export interface Personality {
  actor_id: string;
  name: string;
  age: number;
  cultural_background: string;
  neighborhood: string;
  role: string;
  job_details: string;
  personality_summary: string;
  emotional_range: string;
  backstory_details: string;
  hobbies_and_interests: string[];
  favorite_media: string[];
  food_and_drink: string;
  favorite_local_spots: string[];
  current_life_situations: string[];
  topics_they_bring_up: string[];
  topics_they_avoid: string[];
  pet_peeves: string[];
  humor_style: string;
  daily_routine_notes: string;
  texting_style: TextingStyle;
  how_owner_talks_to_them: string;
  relationship_arc: string;
  sample_phrases: string[];
  suggested_message_volume?: string;
}

export interface DeviceContactRef {
  device_id: string;
  contact_id: string;
}

export interface Contact {
  id: string;
  actor_id: string;
  name: string;
  role: string;
  message_volume: MessageVolume;
  story_arc: string;
  personality: Personality | null;
  shared_with: DeviceContactRef[];
  _volumeManuallySet?: boolean;
}

export interface Device {
  id: string;
  device_label: string;
  owner_name: string;
  owner_actor_id: string;
  owner_story_arc: string;
  generation_mode: GenerationMode;
  role_style: RoleStyle;
  spam_density: SpamDensity;
  owner_personality: Personality | null;
  contacts: Contact[];
}

export interface Connection {
  id: string;
  source_device_id: string;
  source_contact_id: string;
  target_device_id: string;
  target_contact_id: string;
  connection_type: string;
  label?: string;
}

export interface TimelineEvent {
  id: string;
  date: string;
  time: string | null;
  description: string;
  encounter_type: EncounterType;
  device_impacts: Record<string, string>;
  involved_contacts: Record<string, string[]>;
  participants: DeviceContactRef[];
}

export interface GroupChat {
  id: string;
  name: string;
  members: DeviceContactRef[];
  origin_event_id: string;
  start_date: string;
  end_date: string;
  message_volume: MessageVolume;
  vibe: string;
  activation_mode?: string;
  auto_pair_threads?: boolean;
  quality_score?: number;
}

export interface GenerationSettings {
  date_start: string;
  date_end: string;
  messages_per_day_min: number;
  messages_per_day_max: number;
  batch_size: number;
  llm_provider: string;
  llm_model: string;
  temperature: number;
  language: Language;
}

export interface Scenario {
  id: string;
  name: string;
  theme: Theme;
  culture: Culture;
  story_arc: string;
  devices: Device[];
  connections: Connection[];
  timeline_events: TimelineEvent[];
  group_chats: GroupChat[];
  generation_settings: GenerationSettings;
}
